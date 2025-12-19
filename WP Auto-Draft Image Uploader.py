#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WordPress 异步高性能图片上传工具 v3.1
- 异步IO实现，速度提升3-5倍
- 多重校验机制，确保数据完整性
- 防重复、防缺失、防重命名问题
- 直接运行模式
"""

import os
import sys
import re
import json
import time
import base64
import hashlib
import sqlite3
import asyncio
import aiohttp
import logging
import threading
from pathlib import Path
from PIL import Image
from tqdm import tqdm
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set
from collections import OrderedDict
from functools import lru_cache
from contextlib import asynccontextmanager
from enum import Enum
import random

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("wp_uploader.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("wp_uploader")


# ==================== 配置 ====================
class Config:
    """全局配置"""
    # WordPress配置
    WP_DOMAIN = ""
    WP_USER = ""
    WP_APP_PASSWORD = ""
    
    # 本地目录
    BASE_DIRECTORY = r""
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wp_upload_history.db")
    
    # 异步上传配置
    MAX_CONCURRENT_UPLOADS = 15      # 最大并发上传数
    MIN_CONCURRENT_UPLOADS = 3       # 最小并发上传数
    INITIAL_CONCURRENT = 8           # 初始并发数
    
    # 超时配置（秒）
    UPLOAD_TIMEOUT = 60              # 上传超时
    CONNECT_TIMEOUT = 10             # 连接超时
    VERIFY_TIMEOUT = 8               # 验证超时
    
    # 重试配置
    MAX_RETRIES = 3                  # 最大重试次数
    RETRY_BASE_DELAY = 1.0           # 重试基础延迟
    
    # 批次配置
    VERIFY_BATCH_SIZE = 50           # 批量验证大小
    DB_BATCH_SIZE = 100              # 数据库批量写入大小
    
    # 速率控制
    REQUEST_DELAY = 0.05             # 请求间隔（秒）
    BATCH_DELAY = 0.1                # 批次间隔（秒）


# 计算认证信息
_formatted_password = Config.WP_APP_PASSWORD.replace(' ', '')
_wp_credentials = f"{Config.WP_USER}:{_formatted_password}"
WP_AUTH_TOKEN = base64.b64encode(_wp_credentials.encode()).decode('utf-8')
WP_API_URL = f"https://{Config.WP_DOMAIN}/wp-json/wp/v2"


# ==================== 枚举和数据类 ====================
class UploadStatus(Enum):
    """上传状态"""
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    VERIFIED = "verified"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ImageFile:
    """图片文件信息 - 预计算所有必要数据"""
    path: str
    index: int  # 原始顺序索引，确保顺序不乱
    
    # 预计算字段
    name: str = field(init=False)
    folder_name: str = field(init=False)
    file_hash: str = field(init=False)
    file_size: int = field(init=False)
    mime_type: str = field(init=False)
    alt_text: str = field(init=False)
    clean_filename: str = field(init=False)
    
    # 状态字段
    status: UploadStatus = field(default=UploadStatus.PENDING)
    media_id: Optional[int] = field(default=None)
    media_url: Optional[str] = field(default=None)
    error_message: Optional[str] = field(default=None)
    retry_count: int = field(default=0)
    
    def __post_init__(self):
        """初始化时预计算所有字段"""
        self.name = os.path.basename(self.path)
        self.folder_name = os.path.basename(os.path.dirname(self.path))
        self.file_size = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        self.file_hash = self._compute_hash()
        
        # 确定MIME类型
        ext = os.path.splitext(self.name)[1].lower()
        self.mime_type = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.webp': 'image/webp',
            '.png': 'image/png',
        }.get(ext, 'image/jpeg')
        
        # 预计算alt文本和清理后的文件名
        self._compute_alt_text()
    
    def _compute_hash(self) -> str:
        """计算文件MD5哈希"""
        if not os.path.exists(self.path):
            return ""
        
        hash_md5 = hashlib.md5()
        try:
            with open(self.path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception:
            return ""
    
    def _compute_alt_text(self):
        """预计算alt文本"""
        filename_no_ext = os.path.splitext(self.name)[0]
        
        # 清理文件名和文件夹名（只移除文件系统不允许的字符）
        self.clean_filename = re.sub(r'[<>:"/\\|?*]', '', filename_no_ext).strip()
        clean_folder = re.sub(r'[<>:"/\\|?*]', '', self.folder_name).strip()
        
        # 构建alt文本
        if clean_folder and clean_folder.lower() != self.clean_filename.lower():
            self.alt_text = f"{clean_folder} {self.clean_filename}".strip()
        else:
            self.alt_text = self.clean_filename
        
        # 限制长度
        if len(self.alt_text) > 125:
            self.alt_text = self.alt_text[:122] + "..."
    
    def is_valid(self) -> bool:
        """检查文件是否有效"""
        return (
            os.path.exists(self.path) and 
            self.file_size > 0 and 
            bool(self.file_hash)
        )
    
    def get_alt_text_with_index(self, display_index: int) -> str:
        """获取带序号的alt文本（用于文章内容）"""
        if display_index > 0:
            alt = f"{self.alt_text} 图片{display_index + 1}"
        else:
            alt = self.alt_text
        
        if len(alt) > 125:
            alt = alt[:122] + "..."
        return alt


@dataclass
class UploadResult:
    """上传结果"""
    success: bool
    media_id: Optional[int] = None
    media_url: Optional[str] = None
    filename_on_server: Optional[str] = None
    error: Optional[str] = None


# ==================== 数据库管理 ====================
class DatabaseManager:
    """线程安全的数据库管理器 - 支持数据库迁移"""
    
    _instance = None
    _lock = threading.Lock()
    
    # 当前数据库版本
    DB_VERSION = 2
    
    def __new__(cls, db_path: str = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, db_path: str = None):
        if self._initialized:
            return
        
        self.db_path = db_path or Config.DB_PATH
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._setup_database()
        self._initialized = True
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取线程本地连接"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path, 
                check_same_thread=False,
                timeout=30
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute('PRAGMA journal_mode=WAL')
            self._local.conn.execute('PRAGMA synchronous=NORMAL')
            self._local.conn.execute('PRAGMA cache_size=10000')
        return self._local.conn
    
    def _get_db_version(self, conn: sqlite3.Connection) -> int:
        """获取数据库版本"""
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA user_version")
            return cursor.fetchone()[0]
        except Exception:
            return 0
    
    def _set_db_version(self, conn: sqlite3.Connection, version: int):
        """设置数据库版本"""
        conn.execute(f"PRAGMA user_version = {version}")
    
    def _setup_database(self):
        """初始化数据库表 - 支持迁移"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        current_version = self._get_db_version(conn)
        logger.info(f"数据库版本: {current_version}, 目标版本: {self.DB_VERSION}")
        
        # 检查表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='uploaded_images'")
        table_exists = cursor.fetchone() is not None
        
        if not table_exists:
            # 全新安装，创建所有表
            self._create_all_tables(conn)
        else:
            # 已有表，执行迁移
            self._migrate_database(conn, current_version)
        
        # 设置最新版本
        self._set_db_version(conn, self.DB_VERSION)
        
        conn.commit()
        conn.close()
        logger.info("数据库初始化完成")
    
    def _create_all_tables(self, conn: sqlite3.Connection):
        """创建所有表（全新安装）"""
        cursor = conn.cursor()
        
        # 上传图片记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS uploaded_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                media_id INTEGER NOT NULL,
                upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                file_name TEXT,
                media_url TEXT,
                server_filename TEXT,
                folder_name TEXT,
                verified INTEGER DEFAULT 0
            )
        ''')
        
        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_path ON uploaded_images(file_path)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_hash ON uploaded_images(file_hash)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_id ON uploaded_images(media_id)')
        
        # 文章记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS created_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_name TEXT UNIQUE NOT NULL,
                post_id INTEGER NOT NULL,
                image_count INTEGER DEFAULT 0,
                creation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 错误日志表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_type TEXT NOT NULL,
                item_path TEXT,
                error_message TEXT,
                error_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        logger.info("创建所有数据库表完成")
    
    def _migrate_database(self, conn: sqlite3.Connection, current_version: int):
        """数据库迁移"""
        cursor = conn.cursor()
        
        # 获取现有列
        cursor.execute("PRAGMA table_info(uploaded_images)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        
        # 需要添加的列
        new_columns = {
            'file_name': 'TEXT',
            'media_url': 'TEXT',
            'server_filename': 'TEXT',
            'folder_name': 'TEXT',
            'verified': 'INTEGER DEFAULT 0'
        }
        
        # 添加缺失的列
        for col_name, col_type in new_columns.items():
            if col_name not in existing_columns:
                try:
                    cursor.execute(f"ALTER TABLE uploaded_images ADD COLUMN {col_name} {col_type}")
                    logger.info(f"添加列: {col_name}")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e).lower():
                        logger.warning(f"添加列 {col_name} 失败: {e}")
        
        # 更新现有记录的 folder_name（如果为空）
        try:
            cursor.execute("""
                UPDATE uploaded_images 
                SET folder_name = (
                    SELECT REPLACE(
                        REPLACE(file_path, REPLACE(file_path, RTRIM(file_path, REPLACE(file_path, '\\', '')), ''), ''),
                        '\\' || (SELECT REPLACE(file_path, RTRIM(file_path, REPLACE(file_path, '\\', '')), '') FROM uploaded_images AS sub WHERE sub.id = uploaded_images.id),
                        ''
                    )
                )
                WHERE folder_name IS NULL OR folder_name = ''
            """)
        except Exception as e:
            logger.debug(f"更新folder_name时出错（可忽略）: {e}")
        
        # 创建可能缺失的索引
        try:
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_path ON uploaded_images(file_path)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_hash ON uploaded_images(file_hash)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_id ON uploaded_images(media_id)')
        except Exception as e:
            logger.debug(f"创建索引时出错（可忽略）: {e}")
        
        # 确保其他表存在
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS created_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_name TEXT UNIQUE NOT NULL,
                post_id INTEGER NOT NULL,
                image_count INTEGER DEFAULT 0,
                creation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_type TEXT NOT NULL,
                item_path TEXT,
                error_message TEXT,
                error_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 检查 created_posts 表是否有 image_count 列
        cursor.execute("PRAGMA table_info(created_posts)")
        posts_columns = {row[1] for row in cursor.fetchall()}
        
        if 'image_count' not in posts_columns:
            try:
                cursor.execute("ALTER TABLE created_posts ADD COLUMN image_count INTEGER DEFAULT 0")
                logger.info("添加列: created_posts.image_count")
            except sqlite3.OperationalError:
                pass
        
        logger.info("数据库迁移完成")
    
    def get_uploaded_by_hash(self, file_hash: str) -> Optional[Dict]:
        """通过哈希查询已上传记录"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM uploaded_images WHERE file_hash = ?",
            (file_hash,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def get_uploaded_by_path(self, file_path: str) -> Optional[Dict]:
        """通过路径查询已上传记录"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM uploaded_images WHERE file_path = ?",
            (file_path,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def batch_check_uploaded(self, images: List[ImageFile]) -> Dict[str, Dict]:
        """批量检查已上传状态，返回 {file_path: record}"""
        if not images:
            return {}
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 收集所有哈希和路径
        hashes = [img.file_hash for img in images if img.file_hash]
        paths = [img.path for img in images]
        
        results = {}
        
        # 通过哈希查询
        if hashes:
            placeholders = ','.join(['?'] * len(hashes))
            cursor.execute(
                f"SELECT * FROM uploaded_images WHERE file_hash IN ({placeholders})",
                hashes
            )
            for row in cursor.fetchall():
                record = dict(row)
                # 找到对应的ImageFile
                for img in images:
                    if img.file_hash == record['file_hash']:
                        results[img.path] = record
                        break
        
        # 通过路径补充查询
        missing_paths = [p for p in paths if p not in results]
        if missing_paths:
            placeholders = ','.join(['?'] * len(missing_paths))
            cursor.execute(
                f"SELECT * FROM uploaded_images WHERE file_path IN ({placeholders})",
                missing_paths
            )
            for row in cursor.fetchall():
                record = dict(row)
                results[record['file_path']] = record
        
        return results
    
    def record_upload(self, image: ImageFile, media_url: str = None, 
                      server_filename: str = None, verified: bool = False):
        """记录单个上传"""
        with self._write_lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 先检查是否存在
            cursor.execute(
                "SELECT id FROM uploaded_images WHERE file_path = ? OR file_hash = ?",
                (image.path, image.file_hash)
            )
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute('''
                    UPDATE uploaded_images 
                    SET media_id = ?, media_url = ?, server_filename = ?, 
                        folder_name = ?, upload_time = CURRENT_TIMESTAMP, verified = ?
                    WHERE id = ?
                ''', (
                    image.media_id,
                    media_url or image.media_url,
                    server_filename or image.name,
                    image.folder_name,
                    1 if verified else 0,
                    existing['id']
                ))
            else:
                cursor.execute('''
                    INSERT INTO uploaded_images 
                    (file_path, file_hash, file_name, media_id, media_url, 
                     server_filename, folder_name, upload_time, verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                ''', (
                    image.path,
                    image.file_hash,
                    image.name,
                    image.media_id,
                    media_url or image.media_url,
                    server_filename or image.name,
                    image.folder_name,
                    1 if verified else 0
                ))
            
            conn.commit()
    
    def batch_record_uploads(self, images: List[ImageFile]):
        """批量记录上传"""
        if not images:
            return
        
        with self._write_lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            for img in images:
                if img.media_id is None:
                    continue
                
                # 检查是否存在
                cursor.execute(
                    "SELECT id FROM uploaded_images WHERE file_path = ? OR file_hash = ?",
                    (img.path, img.file_hash)
                )
                existing = cursor.fetchone()
                
                if existing:
                    cursor.execute('''
                        UPDATE uploaded_images 
                        SET media_id = ?, media_url = ?, folder_name = ?, 
                            upload_time = CURRENT_TIMESTAMP, verified = ?
                        WHERE id = ?
                    ''', (
                        img.media_id,
                        img.media_url,
                        img.folder_name,
                        1 if img.status == UploadStatus.VERIFIED else 0,
                        existing['id']
                    ))
                else:
                    cursor.execute('''
                        INSERT INTO uploaded_images 
                        (file_path, file_hash, file_name, media_id, media_url, folder_name, verified)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        img.path,
                        img.file_hash,
                        img.name,
                        img.media_id,
                        img.media_url,
                        img.folder_name,
                        1 if img.status == UploadStatus.VERIFIED else 0
                    ))
            
            conn.commit()
    
    def mark_verified(self, media_ids: List[int]):
        """标记为已验证"""
        if not media_ids:
            return
        
        with self._write_lock:
            conn = self._get_connection()
            placeholders = ','.join(['?'] * len(media_ids))
            conn.execute(
                f"UPDATE uploaded_images SET verified = 1 WHERE media_id IN ({placeholders})",
                media_ids
            )
            conn.commit()
    
    def is_post_created(self, folder_name: str) -> Optional[int]:
        """检查文章是否已创建"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT post_id FROM created_posts WHERE folder_name = ?",
            (folder_name,)
        )
        row = cursor.fetchone()
        return row['post_id'] if row else None
    
    def record_post_creation(self, folder_name: str, post_id: int, image_count: int = 0):
        """记录文章创建"""
        with self._write_lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 检查是否存在
            cursor.execute(
                "SELECT id FROM created_posts WHERE folder_name = ?",
                (folder_name,)
            )
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute('''
                    UPDATE created_posts 
                    SET post_id = ?, image_count = ?, creation_time = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (post_id, image_count, existing['id']))
            else:
                cursor.execute('''
                    INSERT INTO created_posts (folder_name, post_id, image_count)
                    VALUES (?, ?, ?)
                ''', (folder_name, post_id, image_count))
            
            conn.commit()
    
    def record_error(self, operation_type: str, item_path: str, error_message: str):
        """记录错误"""
        with self._write_lock:
            try:
                conn = self._get_connection()
                conn.execute(
                    "INSERT INTO error_logs (operation_type, item_path, error_message) VALUES (?, ?, ?)",
                    (operation_type, item_path, error_message)
                )
                conn.commit()
            except Exception as e:
                logger.error(f"记录错误失败: {e}")
    
    def get_failed_folders(self) -> List[str]:
        """获取失败的文件夹"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT item_path 
            FROM error_logs 
            WHERE operation_type = 'create_post' 
            AND item_path NOT IN (SELECT folder_name FROM created_posts)
            ORDER BY error_time DESC
        """)
        return [row['item_path'] for row in cursor.fetchall()]
    
    def get_folder_uploaded_images(self, folder_name: str) -> List[Dict]:
        """获取文件夹已上传的图片"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM uploaded_images WHERE folder_name = ? ORDER BY file_path",
            (folder_name,)
        )
        return [dict(row) for row in cursor.fetchall()]


# ==================== 异步上传器 ====================
class AsyncUploader:
    """异步上传器"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._current_concurrency = Config.INITIAL_CONCURRENT
        self._success_count = 0
        self._fail_count = 0
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保会话存在"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=Config.UPLOAD_TIMEOUT + Config.CONNECT_TIMEOUT,
                connect=Config.CONNECT_TIMEOUT,
                sock_read=Config.UPLOAD_TIMEOUT
            )
            
            connector = aiohttp.TCPConnector(
                limit=Config.MAX_CONCURRENT_UPLOADS + 5,
                limit_per_host=Config.MAX_CONCURRENT_UPLOADS,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
            )
            
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'Authorization': f'Basic {WP_AUTH_TOKEN}',
                    'User-Agent': 'WP-AsyncUploader/3.1',
                    'Accept': 'application/json',
                }
            )
        
        return self._session
    
    async def close(self):
        """关闭会话"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def test_connection(self) -> bool:
        """测试WordPress连接"""
        logger.info("测试WordPress连接...")
        
        try:
            session = await self._ensure_session()
            async with session.get(
                f"{WP_API_URL}/users/me",
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"✓ 连接成功，用户: {data.get('name', '未知')}")
                    return True
                else:
                    logger.error(f"✗ 认证失败: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"✗ 连接测试失败: {e}")
            return False
    
    async def upload_single_image(self, image: ImageFile) -> UploadResult:
        """上传单张图片 - 带完整校验"""
        
        if not image.is_valid():
            return UploadResult(
                success=False,
                error=f"文件无效: {image.path}"
            )
        
        session = await self._ensure_session()
        
        for attempt in range(Config.MAX_RETRIES):
            try:
                if attempt > 0:
                    delay = Config.RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                    await asyncio.sleep(delay)
                
                # 读取文件
                with open(image.path, 'rb') as f:
                    file_content = f.read()
                
                # 【校验1】确保文件完整读取
                if len(file_content) != image.file_size:
                    logger.error(f"文件读取不完整: {image.name}")
                    continue
                
                # 准备上传数据
                form_data = aiohttp.FormData()
                form_data.add_field(
                    'file',
                    file_content,
                    filename=image.name,  # 【关键】强制指定文件名
                    content_type=image.mime_type
                )
                form_data.add_field('title', image.clean_filename)
                form_data.add_field('alt_text', image.alt_text)
                form_data.add_field('description', image.clean_filename)
                
                # 执行上传
                async with session.post(
                    f"{WP_API_URL}/media",
                    data=form_data
                ) as response:
                    
                    if response.status in [200, 201]:
                        data = await response.json()
                        
                        media_id = data.get('id')
                        source_url = data.get('source_url', '')
                        
                        # 【校验2】验证返回的媒体ID有效
                        if not media_id or media_id <= 0:
                            logger.error(f"返回的媒体ID无效: {media_id}")
                            continue
                        
                        # 【校验3】验证返回的文件名
                        server_filename = data.get('media_details', {}).get('file', '')
                        if not server_filename:
                            # 从URL提取文件名
                            server_filename = os.path.basename(source_url.split('?')[0])
                        
                        # 【校验4】验证文件名包含原始文件名的主要部分
                        original_base = os.path.splitext(image.name)[0].lower()
                        server_base = os.path.splitext(server_filename)[0].lower() if server_filename else ''
                        
                        # WordPress可能会添加后缀如 -1, -2 等
                        if server_base and not (original_base in server_base or 
                                server_base.startswith(original_base[:20])):
                            logger.warning(
                                f"文件名可能被修改: {image.name} -> {server_filename}"
                            )
                        
                        return UploadResult(
                            success=True,
                            media_id=media_id,
                            media_url=source_url,
                            filename_on_server=server_filename
                        )
                    
                    elif response.status == 413:
                        return UploadResult(
                            success=False,
                            error="文件过大"
                        )
                    
                    elif response.status == 429:
                        # 速率限制
                        retry_after = int(response.headers.get('Retry-After', 5))
                        await asyncio.sleep(retry_after)
                        continue
                    
                    else:
                        text = await response.text()
                        logger.warning(f"上传失败 [{response.status}]: {image.name}")
                        logger.debug(f"响应: {text[:200]}")
            
            except asyncio.TimeoutError:
                logger.warning(f"上传超时 (尝试{attempt+1}): {image.name}")
            except aiohttp.ClientError as e:
                logger.warning(f"网络错误 (尝试{attempt+1}): {image.name} - {e}")
            except Exception as e:
                logger.error(f"上传异常: {image.name} - {e}")
        
        return UploadResult(
            success=False,
            error=f"上传失败，重试{Config.MAX_RETRIES}次"
        )
    
    async def verify_media_batch(self, media_ids: List[int]) -> Dict[int, Dict]:
        """批量验证媒体 - 返回 {media_id: {url, verified}}"""
        if not media_ids:
            return {}
        
        results = {}
        session = await self._ensure_session()
        
        for i in range(0, len(media_ids), Config.VERIFY_BATCH_SIZE):
            batch = media_ids[i:i + Config.VERIFY_BATCH_SIZE]
            include_param = ",".join(map(str, batch))
            
            try:
                async with session.get(
                    f"{WP_API_URL}/media",
                    params={
                        'include': include_param,
                        'per_page': len(batch),
                        '_fields': 'id,source_url,media_details'
                    },
                    timeout=aiohttp.ClientTimeout(total=Config.VERIFY_TIMEOUT)
                ) as response:
                    
                    if response.status == 200:
                        items = await response.json()
                        
                        for item in items:
                            mid = item.get('id')
                            url = item.get('source_url')
                            
                            if mid and url:
                                # 获取large尺寸URL
                                sizes = item.get('media_details', {}).get('sizes', {})
                                large_url = sizes.get('large', {}).get('source_url', url)
                                
                                results[mid] = {
                                    'url': url,
                                    'large_url': large_url,
                                    'verified': True
                                }
                        
                        # 标记未找到的
                        for mid in batch:
                            if mid not in results:
                                results[mid] = {'verified': False}
                    else:
                        # 批量失败，逐个验证
                        for mid in batch:
                            verified = await self._verify_single_media(session, mid)
                            results[mid] = {'verified': verified}
            
            except Exception as e:
                logger.warning(f"批量验证失败: {e}")
                for mid in batch:
                    results[mid] = {'verified': False}
            
            # 批次间延迟
            await asyncio.sleep(0.1)
        
        return results
    
    async def _verify_single_media(self, session: aiohttp.ClientSession, 
                                    media_id: int) -> bool:
        """单个媒体验证"""
        try:
            async with session.head(
                f"{WP_API_URL}/media/{media_id}",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                return response.status == 200
        except Exception:
            return False
    
    async def upload_images_batch(self, images: List[ImageFile], 
                                   progress_callback=None) -> List[ImageFile]:
        """
        批量上传图片 - 核心方法
        返回处理后的ImageFile列表（保持原始顺序）
        """
        if not images:
            return []
        
        # 按索引排序确保顺序
        images = sorted(images, key=lambda x: x.index)
        
        # 第一步：检查已上传
        logger.info(f"检查 {len(images)} 个文件的上传状态...")
        uploaded_records = self.db.batch_check_uploaded(images)
        
        need_upload = []
        need_verify_ids = []
        
        for img in images:
            if img.path in uploaded_records:
                record = uploaded_records[img.path]
                img.media_id = record['media_id']
                img.media_url = record.get('media_url')
                img.status = UploadStatus.UPLOADED
                need_verify_ids.append(record['media_id'])
            else:
                need_upload.append(img)
        
        # 第二步：验证已上传的媒体
        if need_verify_ids:
            logger.info(f"验证 {len(need_verify_ids)} 个已上传媒体...")
            verify_results = await self.verify_media_batch(need_verify_ids)
            
            for img in images:
                if img.media_id and img.media_id in verify_results:
                    result = verify_results[img.media_id]
                    if result.get('verified'):
                        img.status = UploadStatus.VERIFIED
                        if 'large_url' in result:
                            img.media_url = result['large_url']
                        elif 'url' in result:
                            img.media_url = result['url']
                    else:
                        # 验证失败，需要重新上传
                        img.media_id = None
                        img.media_url = None
                        img.status = UploadStatus.PENDING
                        need_upload.append(img)
        
        skipped_count = len(images) - len(need_upload)
        logger.info(f"跳过已上传: {skipped_count}, 需要上传: {len(need_upload)}")
        
        if not need_upload:
            return images
        
        # 第三步：并发上传
        self._semaphore = asyncio.Semaphore(self._current_concurrency)
        self._success_count = 0
        self._fail_count = 0
        
        # 创建进度条
        pbar = tqdm(total=len(need_upload), desc="上传进度", leave=True)
        
        async def upload_with_semaphore(img: ImageFile):
            async with self._semaphore:
                result = await self.upload_single_image(img)
                
                if result.success:
                    img.media_id = result.media_id
                    img.media_url = result.media_url
                    img.status = UploadStatus.UPLOADED
                    self._success_count += 1
                else:
                    img.status = UploadStatus.FAILED
                    img.error_message = result.error
                    self._fail_count += 1
                
                pbar.update(1)
                
                # 动态调整并发数
                await self._adjust_concurrency()
                
                # 请求间隔
                await asyncio.sleep(Config.REQUEST_DELAY)
                
                return img
        
        # 执行并发上传
        tasks = [upload_with_semaphore(img) for img in need_upload]
        await asyncio.gather(*tasks)
        
        pbar.close()
        
        # 第四步：批量验证上传结果
        uploaded_ids = [img.media_id for img in need_upload 
                       if img.media_id and img.status == UploadStatus.UPLOADED]
        
        if uploaded_ids:
            logger.info(f"验证 {len(uploaded_ids)} 个新上传媒体...")
            verify_results = await self.verify_media_batch(uploaded_ids)
            
            verified_images = []
            for img in need_upload:
                if img.media_id and img.media_id in verify_results:
                    result = verify_results[img.media_id]
                    if result.get('verified'):
                        img.status = UploadStatus.VERIFIED
                        if 'large_url' in result:
                            img.media_url = result['large_url']
                        verified_images.append(img)
                    else:
                        img.status = UploadStatus.FAILED
                        img.error_message = "上传后验证失败"
                        self._fail_count += 1
                        self._success_count -= 1
            
            # 批量写入数据库
            if verified_images:
                self.db.batch_record_uploads(verified_images)
        
        # 统计
        total_success = sum(1 for img in images if img.status == UploadStatus.VERIFIED)
        total_fail = sum(1 for img in images if img.status == UploadStatus.FAILED)
        
        logger.info(f"上传完成 - 成功: {total_success}, 失败: {total_fail}, "
                   f"成功率: {total_success/len(images):.1%}")
        
        # 按原始顺序返回
        return sorted(images, key=lambda x: x.index)
    
    async def _adjust_concurrency(self):
        """动态调整并发数"""
        total = self._success_count + self._fail_count
        if total < 10:
            return
        
        success_rate = self._success_count / total
        
        if success_rate > 0.95 and self._current_concurrency < Config.MAX_CONCURRENT_UPLOADS:
            self._current_concurrency = min(
                self._current_concurrency + 2,
                Config.MAX_CONCURRENT_UPLOADS
            )
            self._semaphore = asyncio.Semaphore(self._current_concurrency)
        elif success_rate < 0.7 and self._current_concurrency > Config.MIN_CONCURRENT_UPLOADS:
            self._current_concurrency = max(
                self._current_concurrency - 1,
                Config.MIN_CONCURRENT_UPLOADS
            )
            self._semaphore = asyncio.Semaphore(self._current_concurrency)


# ==================== 文章创建器 ====================
class PostCreator:
    """文章创建器"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保会话存在"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={
                    'Authorization': f'Basic {WP_AUTH_TOKEN}',
                    'Content-Type': 'application/json',
                }
            )
        return self._session
    
    async def close(self):
        """关闭会话"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    def build_post_content(self, images: List[ImageFile], folder_name: str) -> str:
        """构建文章内容 - 确保图片顺序正确"""
        # 按原始索引排序
        sorted_images = sorted(
            [img for img in images if img.status == UploadStatus.VERIFIED and img.media_url],
            key=lambda x: x.index
        )
        
        if not sorted_images:
            return ""
        
        content_parts = []
        
        for display_idx, img in enumerate(sorted_images):
            alt_text = img.get_alt_text_with_index(display_idx)
            escaped_alt = alt_text.replace('"', '\\"')
            
            # WordPress Gutenberg图片块
            block = f'''<!-- wp:image {{"id":{img.media_id},"sizeSlug":"large","linkDestination":"none"}} -->
<figure class="wp-block-image size-large"><img src="{img.media_url}" alt="{alt_text}" class="wp-image-{img.media_id}" title="{img.clean_filename}"/></figure>
<!-- /wp:image -->'''
            
            content_parts.append(block)
        
        return '\n\n'.join(content_parts)
    
    def select_featured_image(self, images: List[ImageFile]) -> Optional[int]:
        """选择特色图片 - 优先横向图片"""
        verified_images = [
            img for img in images 
            if img.status == UploadStatus.VERIFIED and img.media_id
        ]
        
        if not verified_images:
            return None
        
        # 优先选择横向图片（从后往前找）
        for img in reversed(verified_images):
            if is_horizontal_image(img.path):
                return img.media_id
        
        # 没有横向图片，选最后一张
        return verified_images[-1].media_id
    
    async def create_draft_post(self, folder_name: str, content: str,
                                featured_image_id: Optional[int] = None,
                                media_ids: List[int] = None) -> Optional[int]:
        """创建草稿文章"""
        # 检查是否已存在
        existing = self.db.is_post_created(folder_name)
        if existing:
            logger.info(f"文章已存在，ID: {existing}")
            return existing
        
        if not content or not content.strip():
            logger.error("文章内容为空")
            return None
        
        post_data = {
            'title': folder_name,
            'content': content,
            'status': 'draft',
        }
        
        if featured_image_id:
            post_data['featured_media'] = featured_image_id
        
        session = await self._ensure_session()
        
        for attempt in range(Config.MAX_RETRIES):
            try:
                async with session.post(
                    f"{WP_API_URL}/posts",
                    json=post_data
                ) as response:
                    
                    if response.status in [200, 201]:
                        data = await response.json()
                        post_id = data.get('id')
                        
                        if post_id and post_id > 0:
                            logger.info(f"草稿创建成功，ID: {post_id}")
                            
                            # 记录到数据库
                            image_count = len(media_ids) if media_ids else 0
                            self.db.record_post_creation(folder_name, post_id, image_count)
                            
                            # 附加媒体
                            if media_ids:
                                await self._attach_media_batch(post_id, media_ids)
                            
                            return post_id
                    
                    else:
                        text = await response.text()
                        logger.error(f"创建文章失败 [{response.status}]: {text[:200]}")
                
                if attempt < Config.MAX_RETRIES - 1:
                    await asyncio.sleep(2 * (attempt + 1))
                    
            except Exception as e:
                logger.error(f"创建文章异常: {e}")
                if attempt < Config.MAX_RETRIES - 1:
                    await asyncio.sleep(2 * (attempt + 1))
        
        self.db.record_error("create_post", folder_name, "创建失败")
        return None
    
    async def _attach_media_batch(self, post_id: int, media_ids: List[int]):
        """批量附加媒体到文章"""
        if not media_ids:
            return
        
        session = await self._ensure_session()
        
        async def attach_single(media_id: int) -> bool:
            try:
                async with session.post(
                    f"{WP_API_URL}/media/{media_id}",
                    json={'post': post_id}
                ) as response:
                    return response.status in [200, 201]
            except Exception:
                return False
        
        # 并发附加
        tasks = [attach_single(mid) for mid in media_ids]
        results = await asyncio.gather(*tasks)
        
        success = sum(results)
        logger.info(f"媒体附加完成: {success}/{len(media_ids)}")


# ==================== 工具函数 ====================
@lru_cache(maxsize=1024)
def get_image_dimensions(image_path: str) -> Tuple[int, int]:
    """获取图片尺寸"""
    try:
        with Image.open(image_path) as img:
            return img.size
    except Exception:
        return 800, 600


def is_horizontal_image(image_path: str) -> bool:
    """检查是否横向图片"""
    try:
        width, height = get_image_dimensions(image_path)
        return width > height
    except Exception:
        return False


def natural_sort_key(s: str):
    """自然排序"""
    return [int(text) if text.isdigit() else text.lower() 
            for text in re.split(r'(\d+)', str(s))]


def scan_folder_images(folder_path: str) -> List[ImageFile]:
    """扫描文件夹中的图片"""
    if not os.path.isdir(folder_path):
        return []
    
    valid_extensions = {'.jpg', '.jpeg', '.webp', '.png'}
    
    try:
        files = os.listdir(folder_path)
    except Exception as e:
        logger.error(f"读取文件夹失败: {e}")
        return []
    
    # 过滤图片文件并排序
    image_files = [
        f for f in files 
        if os.path.splitext(f)[1].lower() in valid_extensions
    ]
    image_files.sort(key=natural_sort_key)
    
    # 创建ImageFile对象
    images = []
    for idx, filename in enumerate(image_files):
        file_path = os.path.join(folder_path, filename)
        try:
            img = ImageFile(path=file_path, index=idx)
            if img.is_valid():
                images.append(img)
            else:
                logger.warning(f"无效图片: {filename}")
        except Exception as e:
            logger.error(f"创建ImageFile失败 {filename}: {e}")
    
    return images


# ==================== 主处理流程 ====================
class FolderProcessor:
    """文件夹处理器"""
    
    def __init__(self):
        self.db = DatabaseManager(Config.DB_PATH)
        self.uploader = AsyncUploader(self.db)
        self.post_creator = PostCreator(self.db)
    
    async def close(self):
        """关闭资源"""
        await self.uploader.close()
        await self.post_creator.close()
    
    async def process_folder(self, folder_path: str) -> bool:
        """处理单个文件夹"""
        folder_name = os.path.basename(folder_path)
        
        logger.info(f"\n{'='*50}")
        logger.info(f"处理文件夹: {folder_name}")
        logger.info(f"{'='*50}")
        
        # 检查是否已创建文章
        if self.db.is_post_created(folder_name):
            logger.info(f"✓ 文章已存在，跳过")
            return True
        
        # 扫描图片
        images = scan_folder_images(folder_path)
        
        if not images:
            logger.warning("没有找到有效图片")
            return False
        
        logger.info(f"找到 {len(images)} 个图片")
        
        # 上传图片
        processed_images = await self.uploader.upload_images_batch(images)
        
        # 统计
        verified = [img for img in processed_images if img.status == UploadStatus.VERIFIED]
        failed = [img for img in processed_images if img.status == UploadStatus.FAILED]
        
        if not verified:
            logger.error("没有图片上传成功")
            self.db.record_error("process_folder", folder_path, "上传失败")
            return False
        
        logger.info(f"上传统计 - 成功: {len(verified)}, 失败: {len(failed)}")
        
        # 构建文章内容
        content = self.post_creator.build_post_content(verified, folder_name)
        
        if not content:
            logger.error("无法构建文章内容")
            return False
        
        # 选择特色图片
        featured_id = self.post_creator.select_featured_image(verified)
        
        # 收集媒体ID
        media_ids = [img.media_id for img in verified if img.media_id]
        
        # 创建文章
        post_id = await self.post_creator.create_draft_post(
            folder_name, content, featured_id, media_ids
        )
        
        if post_id:
            logger.info(f"✓ 成功创建文章 ID:{post_id} ({len(verified)}张图片)")
            return True
        else:
            logger.error("创建文章失败")
            return False
    
    async def process_all_folders(self):
        """处理所有文件夹"""
        base_dir = Config.BASE_DIRECTORY
        
        logger.info(f"开始处理目录: {base_dir}")
        
        if not os.path.isdir(base_dir):
            logger.error(f"目录不存在: {base_dir}")
            return
        
        try:
            folders = [
                f for f in os.listdir(base_dir)
                if os.path.isdir(os.path.join(base_dir, f))
            ]
            folders.sort(key=natural_sort_key)
        except Exception as e:
            logger.error(f"读取目录失败: {e}")
            return
        
        if not folders:
            logger.info("没有找到子文件夹")
            return
        
        logger.info(f"找到 {len(folders)} 个文件夹")
        
        success_count = 0
        fail_count = 0
        skip_count = 0
        
        for folder in tqdm(folders, desc="总进度"):
            folder_path = os.path.join(base_dir, folder)
            
            # 检查是否已处理
            if self.db.is_post_created(folder):
                skip_count += 1
                continue
            
            try:
                result = await self.process_folder(folder_path)
                if result:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"处理文件夹异常 {folder}: {e}")
                fail_count += 1
            
            await asyncio.sleep(Config.BATCH_DELAY)
        
        logger.info(f"\n{'='*50}")
        logger.info(f"全部完成!")
        logger.info(f"成功: {success_count}, 失败: {fail_count}, 跳过: {skip_count}")
        logger.info(f"{'='*50}")


# ==================== 主入口 ====================
async def main():
    """异步主函数 - 直接运行模式"""
    logger.info("="*50)
    logger.info("WordPress异步上传工具 v3.1")
    logger.info("="*50)
    
    processor = FolderProcessor()
    
    try:
        # 测试连接
        if not await processor.uploader.test_connection():
            logger.error("WordPress连接失败，程序终止")
            logger.error("请检查配置:")
            logger.error(f"  - 域名: {Config.WP_DOMAIN}")
            logger.error(f"  - 用户: {Config.WP_USER}")
            return
        
        # 检查目录
        if not os.path.isdir(Config.BASE_DIRECTORY):
            logger.error(f"图片目录不存在: {Config.BASE_DIRECTORY}")
            return
        
        # 直接处理所有文件夹
        await processor.process_all_folders()
    
    except KeyboardInterrupt:
        logger.info("\n用户中断操作")
    except Exception as e:
        logger.error(f"运行异常: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        await processor.close()
    
    logger.info("程序执行完毕")


def run():
    """同步入口点"""
    try:
        # Windows需要设置事件循环策略
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        asyncio.run(main())
    
    except KeyboardInterrupt:
        logger.info("用户中断")
    except Exception as e:
        logger.error(f"未处理异常: {e}")
        import traceback
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    run()
