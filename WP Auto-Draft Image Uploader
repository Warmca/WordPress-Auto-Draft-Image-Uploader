import os
import requests
import base64
import json
import hashlib
from PIL import Image
import concurrent.futures
import time
import sqlite3
import re
import logging
import sys
from tqdm import tqdm
from datetime import datetime
from functools import lru_cache
from dataclasses import dataclass
import concurrent.futures
import threading
from queue import Queue
import random
####2025-06-02 updata####
# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("wp_uploader.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("wp_uploader")

# WordPress站点配置
wp_domain = "n"
wp_url = f"https://{wp_domain}/wp-json/wp/v2"
wp_user = ""
wp_app_password = ""

# 格式化应用密码（移除空格）并创建认证头
formatted_password = wp_app_password.replace(' ', '')
wp_credentials = f"{wp_user}:{formatted_password}"
wp_token = base64.b64encode(wp_credentials.encode())
wp_auth_header = {'Authorization': 'Basic ' + wp_token.decode('utf-8')}

# 本地包含图片文件夹的目录
base_directory = r"C:\Users\Tongxi\Pictures\写真预览"

# 本地数据库路径
db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wp_upload_history.db")

# 连接池优化配置 - 2025-06-07 新增
def create_optimized_session():
    """创建优化的请求会话"""
    session = requests.Session()
    session.headers.update(wp_auth_header)
    
    # 优化连接池配置
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=15,      # 增加连接池
        pool_maxsize=25,         # 增加最大连接数
        max_retries=requests.adapters.Retry(
            total=3,
            backoff_factor=0.5,   # 减少退避时间
            status_forcelist=[500, 502, 503, 504, 520, 521, 522, 523, 524, 429],
            allowed_methods=["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE", "POST"]
        )
    )
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    
    # 优化请求头
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache',
        'Keep-Alive': 'timeout=30, max=100'
    })
    
    return session

# 替换原有的session创建
session = create_optimized_session()

# 线程本地存储用于每个线程的会话
thread_local = threading.local()

def get_thread_session():
    """获取线程本地的会话对象"""
    if not hasattr(thread_local, 'session'):
        thread_local.session = create_optimized_session()
    return thread_local.session

@dataclass
class ImageInfo:
    """图片信息数据类"""
    path: str
    hash: str
    name: str
    width: int = 0
    height: int = 0

class DatabaseManager:
    """数据库管理类"""
    def __init__(self, db_path):
        self.db_path = db_path
        self.setup_database()

    def get_connection(self):
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def setup_database(self):
        """设置SQLite数据库"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # 创建上传图片记录表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS uploaded_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT,
            file_hash TEXT,
            media_id INTEGER,
            upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # 创建索引以加快查询
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_path ON uploaded_images (file_path)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_hash ON uploaded_images (file_hash)')
        
        # 创建文章记录表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS created_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_name TEXT UNIQUE,
            post_id INTEGER,
            creation_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # 创建错误日志表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_type TEXT,
            item_path TEXT,
            error_message TEXT,
            error_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("数据库设置完成")

    def record_error(self, operation_type, item_path, error_message):
        """记录错误到数据库"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO error_logs (operation_type, item_path, error_message) VALUES (?, ?, ?)",
            (operation_type, item_path, error_message)
        )
        conn.commit()
        conn.close()

    def is_image_uploaded(self, file_path, file_hash=None):
        """检查图片是否已经上传"""
        if file_hash is None:
            file_hash = get_file_hash(file_path)
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # 先检查完整文件路径
        cursor.execute("SELECT media_id FROM uploaded_images WHERE file_path = ?", (file_path,))
        result = cursor.fetchone()
        
        # 如果找不到，则检查文件哈希
        if not result:
            cursor.execute("SELECT media_id FROM uploaded_images WHERE file_hash = ?", (file_hash,))
            result = cursor.fetchone()
        
        conn.close()
        
        return result['media_id'] if result else None

    def record_image_upload(self, file_path, media_id, file_hash=None):
        """记录已上传的图片"""
        if file_hash is None:
            file_hash = get_file_hash(file_path)
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # 先检查是否已存在记录
        cursor.execute("SELECT id FROM uploaded_images WHERE file_path = ? OR file_hash = ?", 
                    (file_path, file_hash))
        existing = cursor.fetchone()
        
        if existing:
            # 更新已有记录
            cursor.execute("UPDATE uploaded_images SET media_id = ?, upload_time = CURRENT_TIMESTAMP WHERE id = ?",
                        (media_id, existing['id']))
        else:
            # 插入新记录
            cursor.execute("INSERT INTO uploaded_images (file_path, file_hash, media_id) VALUES (?, ?, ?)",
                        (file_path, file_hash, media_id))
        
        conn.commit()
        conn.close()

    def is_post_created(self, folder_name):
        """检查是否已经为文件夹创建了文章"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT post_id FROM created_posts WHERE folder_name = ?", (folder_name,))
        result = cursor.fetchone()
        
        conn.close()
        
        return result['post_id'] if result else None

    def record_post_creation(self, folder_name, post_id):
        """记录已创建的文章"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # 检查记录是否已存在
        cursor.execute("SELECT id FROM created_posts WHERE folder_name = ?", (folder_name,))
        existing = cursor.fetchone()
        
        if existing:
            # 更新已有记录
            cursor.execute("UPDATE created_posts SET post_id = ?, creation_time = CURRENT_TIMESTAMP WHERE id = ?",
                        (post_id, existing['id']))
        else:
            # 插入新记录
            cursor.execute("INSERT INTO created_posts (folder_name, post_id) VALUES (?, ?)",
                        (folder_name, post_id))
        
        conn.commit()
        conn.close()

    def get_failed_folders(self):
        """获取上传失败的文件夹"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT item_path 
            FROM error_logs 
            WHERE operation_type = 'create_post' 
            AND item_path NOT IN (SELECT folder_name FROM created_posts)
            ORDER BY error_time DESC
        """)
        results = cursor.fetchall()
        
        conn.close()
        
        return [result['item_path'] for result in results]

@lru_cache(maxsize=512)
def get_file_hash(file_path):
    """计算文件的MD5哈希值（带缓存）"""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logger.error(f"计算文件哈希时出错 {file_path}: {str(e)}")
        return None

@lru_cache(maxsize=512)
def get_image_dimensions(image_path):
    """获取图片尺寸（带缓存）"""
    try:
        if not os.path.exists(image_path):
            logger.error(f"图片文件不存在: {image_path}")
            return 800, 600  # 返回默认值
            
        if os.path.getsize(image_path) <= 0:
            logger.error(f"图片文件大小为0: {image_path}")
            return 800, 600  # 返回默认值
            
        with Image.open(image_path) as img:
            width, height = img.size
            
            # 验证获取的尺寸是否合理
            if width <= 0 or height <= 0 or width > 10000 or height > 10000:
                logger.warning(f"图片尺寸异常: {width}x{height}, 使用默认值")
                return 800, 600
                
            return width, height
    except Exception as e:
        logger.error(f"获取图片尺寸时出错 {image_path}: {str(e)}")
        return 800, 600  # 返回一个合理的默认值

def is_horizontal_image(image_path):
    """检查图片是否为横向"""
    try:
        width, height = get_image_dimensions(image_path)
        return width > height
    except Exception as e:
        logger.error(f"检查图片方向时出错: {str(e)}")
        return False
# 在test_wp_access函数之前添加这些新函数



def reset_session():
    """重置会话连接"""
    global session
    logger.info("重置会话连接...")
    session.close()
    
    # 重新创建会话
    session = requests.Session()
    session.headers.update(wp_auth_header)
    
    # 重新配置适配器
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=3,
        pool_maxsize=5,
        max_retries=requests.adapters.Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[500, 502, 503, 504, 520, 521, 522, 523, 524],
            allowed_methods=["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE", "POST"]
        )
    )
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache'
    })
    
def test_wp_access():
    """测试基本WordPress访问（不带认证）"""
    logger.info("测试WordPress站点基本访问...")
    
    # 首先尝试连接检测
    if not test_connection_with_fallback():
        logger.error("基础连接测试失败")
        return False
    
    # 多次尝试访问
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"尝试访问站点，第 {attempt}/{max_attempts} 次")
            response = requests.get(
                f"https://{wp_domain}", 
                timeout=20,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            )
            if response.status_code == 200:
                logger.info(f"站点访问成功: https://{wp_domain}")
                return True
            else:
                logger.warning(f"站点访问返回状态码: {response.status_code}")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"连接错误 (尝试 {attempt}): {str(e)}")
            if "10054" in str(e) or "Connection aborted" in str(e):
                logger.warning("检测到连接被重置，可能是网络不稳定")
        except requests.exceptions.Timeout as e:
            logger.error(f"连接超时 (尝试 {attempt}): {str(e)}")
        except Exception as e:
            logger.error(f"站点访问异常 (尝试 {attempt}): {str(e)}")
        
        # 如果不是最后一次尝试，等待后重试
        if attempt < max_attempts:
            wait_time = attempt * 3
            logger.info(f"等待 {wait_time} 秒后重试...")
            time.sleep(wait_time)
    
    logger.error("所有访问尝试均失败")
    return False

# 简化的网络测试函数 - 2025-06-07 修改
def test_wp_connection():
    """简化的WordPress连接测试"""
    logger.info("测试WordPress连接...")
    
    try:
        # 直接测试REST API认证
        response = session.get(f"{wp_url}/users/me", timeout=10)
        
        if response.status_code == 200:
            user_data = response.json()
            logger.info(f"连接成功，用户: {user_data.get('name', '未知用户')}")
            return True
        else:
            logger.error(f"认证失败，状态码: {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"连接测试失败: {str(e)}")
        return False

def upload_single_file_concurrent(file_info, db_manager, max_retries=4):
    """并发安全的单文件上传函数 - 优化版本，增强alt文本功能"""
    thread_session = get_thread_session()
    logger.debug(f"[线程{threading.current_thread().ident % 10000}] 开始上传: {file_info.name}")
    
    # 预检查避免无效上传
    if not os.path.exists(file_info.path) or os.path.getsize(file_info.path) <= 0:
        logger.error(f"文件无效或不存在: {file_info.path}")
        db_manager.record_error("upload_media", file_info.path, "文件无效")
        return None
    
    for retry in range(max_retries):
        try:
            if retry > 0:
                # 指数退避 + 随机抖动避免雷群效应
                base_wait = min(2 ** retry, 8)
                jitter = random.uniform(0.1, 0.5)
                wait_time = base_wait + jitter
                logger.info(f"[重试{retry+1}] {file_info.name} - 等待{wait_time:.1f}秒")
                time.sleep(wait_time)
            
            # 准备上传数据
            with open(file_info.path, 'rb') as file_obj:
                file_content = file_obj.read()
                
                if len(file_content) != os.path.getsize(file_info.path):
                    logger.error(f"文件读取长度不匹配: {file_info.path}")
                    continue
                
                file_ext = os.path.splitext(file_info.name)[1].lower()
                mime_type = 'image/webp' if file_ext == '.webp' else 'image/jpeg'
                
                # 增强的SEO友好alt文本生成
                filename_without_ext = os.path.splitext(file_info.name)[0]
                # 获取文件夹名称作为上下文
                folder_name = os.path.basename(os.path.dirname(file_info.path))
                
                # 保留更多字符，只移除真正有问题的字符
                clean_filename = re.sub(r'[<>:"/\\|?*]', '', filename_without_ext).strip()
                clean_folder = re.sub(r'[<>:"/\\|?*]', '', folder_name).strip()
                
                # 构建更丰富的alt文本
                if clean_folder and clean_folder != clean_filename:
                    alt_text = f"{clean_folder} {clean_filename}".strip()
                else:
                    alt_text = clean_filename
                
                # 限制alt文本长度，SEO最佳实践建议不超过125个字符
                if len(alt_text) > 125:
                    alt_text = alt_text[:122] + "..."
                
                files = {'file': (file_info.name, file_content, mime_type)}
                
                # 准备媒体数据，包含增强的alt文本和标题
                media_data = {
                    'alt_text': alt_text,
                    'title': clean_filename,  # 添加标题
                    'caption': '',
                    'description': clean_filename
                }
                
                logger.debug(f"为图片 {file_info.name} 生成alt文本: {alt_text}")
                
                # 上传请求，同时发送媒体数据
                response = thread_session.post(
                    f"{wp_url}/media",
                    files=files,
                    data=media_data,
                    timeout=(10, 60)  # 连接超时10秒，读取超时60秒
                )
                
                if response.status_code in [200, 201]:
                    try:
                        response_data = response.json()
                        media_id = response_data.get('id')
                        
                        if not media_id or media_id <= 0:
                            logger.error(f"无效媒体ID: {media_id}")
                            continue
                        
                        # 【关键】双重验证媒体可访问性
                        if not verify_media_accessible_concurrent(media_id, thread_session):
                            logger.warning(f"媒体验证失败: {media_id}, 重试...")
                            continue
                        
                        logger.info(f"✓ [线程{threading.current_thread().ident % 10000}] 上传成功: {file_info.name} -> ID:{media_id}")
                        db_manager.record_image_upload(file_info.path, media_id, file_info.hash)
                        return (file_info.path, media_id)
                        
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON解析错误: {str(e)}")
                        continue
                        
                elif response.status_code == 413:
                    logger.error(f"文件过大: {file_info.name}")
                    db_manager.record_error("upload_media", file_info.path, "文件过大")
                    return None
                elif response.status_code == 429:
                    # 速率限制，增加等待时间
                    wait_time = (retry + 1) * 5
                    logger.warning(f"速率限制，等待{wait_time}秒: {file_info.name}")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.warning(f"上传失败 {response.status_code}: {file_info.name}")
                    
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"连接错误 (重试{retry+1}): {file_info.name} - {str(e)}")
            if "10054" in str(e) or "Connection aborted" in str(e):
                # 连接被重置，重新创建线程会话
                thread_local.session = create_optimized_session()
        except requests.exceptions.Timeout as e:
            logger.warning(f"超时错误 (重试{retry+1}): {file_info.name} - {str(e)}")
        except Exception as e:
            logger.error(f"上传异常 (重试{retry+1}): {file_info.name} - {str(e)}")
    
    # 所有重试失败
    logger.error(f"✗ 上传最终失败: {file_info.name}")
    db_manager.record_error("upload_media", file_info.path, f"上传失败，重试{max_retries}次")
    return None

def verify_media_accessible_concurrent(media_id, session_obj):
    """并发安全的媒体验证函数"""
    try:
        response = session_obj.get(f"{wp_url}/media/{media_id}", timeout=5)
        if response.status_code == 200:
            media_data = response.json()
            return 'source_url' in media_data and media_data['source_url']
        return False
    except Exception as e:
        logger.debug(f"验证媒体{media_id}时出错: {str(e)}")
        return False

def update_media_alt_text(media_id, file_path, session_obj=None, max_retries=3):
    """更新媒体项的alt文本"""
    if session_obj is None:
        session_obj = session
    
    # 生成SEO友好的alt文本
    filename_without_ext = os.path.splitext(os.path.basename(file_path))[0]
    clean_filename = re.sub(r'[<>:"/\\|?*]', '', filename_without_ext).strip()
    alt_text = clean_filename.strip()
    
    update_data = {
        'alt_text': alt_text,
        'description': clean_filename
    }
    
    for attempt in range(max_retries):
        try:
            response = session_obj.post(
                f"{wp_url}/media/{media_id}",
                json=update_data,
                timeout=10
            )
            
            if response.status_code in [200, 201]:
                logger.debug(f"成功更新媒体 {media_id} 的alt文本: {alt_text}")
                return True
            else:
                logger.warning(f"更新媒体 {media_id} alt文本失败: {response.status_code}")
                
        except Exception as e:
            logger.error(f"更新媒体 {media_id} alt文本时出错: {str(e)}")
            
        if attempt < max_retries - 1:
            time.sleep(1)
    
    return False

def batch_update_media_alt_text(media_ids_dict, db_manager):
    """批量更新媒体alt文本"""
    logger.info(f"开始批量更新 {len(media_ids_dict)} 个媒体的alt文本...")
    
    success_count = 0
    failed_count = 0
    
    for file_path, media_id in media_ids_dict.items():
        if not media_id:
            continue
            
        if update_media_alt_text(media_id, file_path):
            success_count += 1
        else:
            failed_count += 1
            db_manager.record_error("update_alt_text", file_path, f"更新媒体{media_id}的alt文本失败")
    
    logger.info(f"alt文本更新完成 - 成功: {success_count}, 失败: {failed_count}")
    return success_count, failed_count

def batch_upload_media_concurrent(file_paths, db_manager, initial_workers=4, max_workers=8):
    """智能并发批量上传 - 2025-06-07 全新版本"""
    logger.info(f"开始智能并发上传 {len(file_paths)} 个图片...")
    
    media_ids = {}
    failed_uploads = []
    
    # 第一步：快速检查已上传图片
    new_file_paths = []
    logger.info("检查已上传图片...")
    
    with tqdm(total=len(file_paths), desc="检查上传状态") as pbar:
        for file_path in file_paths:
            file_hash = get_file_hash(file_path)
            existing_media_id = db_manager.is_image_uploaded(file_path, file_hash)
            
            if existing_media_id and verify_media_accessible_concurrent(existing_media_id, session):
                media_ids[file_path] = existing_media_id
                logger.debug(f"跳过已上传: {os.path.basename(file_path)}")
            else:
                width, height = get_image_dimensions(file_path)
                new_file_paths.append(ImageInfo(
                    path=file_path,
                    hash=file_hash,
                    name=os.path.basename(file_path),
                    width=width,
                    height=height
                ))
            pbar.update(1)
    
    total_new = len(new_file_paths)
    logger.info(f"需要上传 {total_new} 个新图片")
    
    if total_new == 0:
        return media_ids, failed_uploads
    
    # 第二步：智能并发上传
    success_count = 0
    current_workers = initial_workers
    
    # 动态调整并发数的指标
    success_rate_window = []
    
    # 分批并发处理，避免过载
    batch_size = max_workers * 3  # 每批处理的文件数
    
    for batch_start in range(0, total_new, batch_size):
        batch_end = min(batch_start + batch_size, total_new)
        current_batch = new_file_paths[batch_start:batch_end]
        
        logger.info(f"处理批次: {batch_start//batch_size + 1}/{(total_new+batch_size-1)//batch_size} "
                   f"({len(current_batch)} 个文件, {current_workers} 个工作线程)")
        
        batch_success = 0
        batch_total = len(current_batch)
        
        # 使用线程池并发上传当前批次
        with concurrent.futures.ThreadPoolExecutor(max_workers=current_workers) as executor:
            # 提交所有任务
            future_to_file = {
                executor.submit(upload_single_file_concurrent, file_info, db_manager): file_info 
                for file_info in current_batch
            }
            
            # 处理完成的任务
            with tqdm(total=len(current_batch), desc=f"批次{batch_start//batch_size + 1}上传") as pbar:
                for future in concurrent.futures.as_completed(future_to_file):
                    file_info = future_to_file[future]
                    try:
                        result = future.result(timeout=120)  # 2分钟超时
                        if result:
                            file_path, media_id = result
                            media_ids[file_path] = media_id
                            batch_success += 1
                            success_count += 1
                        else:
                            failed_uploads.append(file_info.path)
                    except concurrent.futures.TimeoutError:
                        logger.error(f"上传超时: {file_info.name}")
                        failed_uploads.append(file_info.path)
                    except Exception as e:
                        logger.error(f"上传任务异常: {file_info.name} - {str(e)}")
                        failed_uploads.append(file_info.path)
                    
                    pbar.update(1)
        
        # 计算当前批次成功率
        batch_success_rate = batch_success / batch_total if batch_total > 0 else 0
        success_rate_window.append(batch_success_rate)
        
        # 保持窗口大小
        if len(success_rate_window) > 3:
            success_rate_window.pop(0)
        
        avg_success_rate = sum(success_rate_window) / len(success_rate_window)
        
        logger.info(f"批次完成 - 成功率: {batch_success_rate:.1%}, 平均成功率: {avg_success_rate:.1%}")
        
        # 【智能调整】根据成功率动态调整并发数
        if avg_success_rate > 0.85 and current_workers < max_workers:
            current_workers = min(current_workers + 1, max_workers)
            logger.info(f"成功率高，增加并发数至: {current_workers}")
        elif avg_success_rate < 0.6 and current_workers > 2:
            current_workers = max(current_workers - 1, 2)
            logger.info(f"成功率低，减少并发数至: {current_workers}")
        
        # 批次间短暂休息
        if batch_end < total_new:
            time.sleep(0.5)
    
    # 最终统计
    total_success = len([v for v in media_ids.values() if v])
    final_success_rate = total_success / len(file_paths) if file_paths else 0
    
    logger.info(f"并发上传完成！")
    logger.info(f"总文件: {len(file_paths)}, 成功: {total_success}, 失败: {len(failed_uploads)}")
    logger.info(f"整体成功率: {final_success_rate:.1%}")
    
    if failed_uploads:
        logger.warning("失败的文件:")
        for failed_path in failed_uploads[:10]:  # 只显示前10个
            logger.warning(f"  - {os.path.basename(failed_path)}")
        if len(failed_uploads) > 10:
            logger.warning(f"  ... 还有 {len(failed_uploads)-10} 个文件上传失败")
    
    return media_ids, failed_uploads


def create_draft_post(title, content, db_manager, featured_image_id=None, attached_media_ids=None, max_retries=3):
    """创建WordPress草稿文章并附加媒体"""
    logger.info(f"创建草稿文章: {title}")
    
    # 检查是否已经创建了文章
    existing_post_id = db_manager.is_post_created(title)
    if existing_post_id:
        logger.info(f"文章已存在，ID: {existing_post_id}")
        return existing_post_id
    
    # 检查内容是否为空
    if not content or len(content.strip()) == 0:
        logger.error("文章内容为空，创建文章失败")
        db_manager.record_error("create_post", title, "文章内容为空")
        return None
    
    post_data = {
        'title': title,
        'content': content,
        'status': 'draft',
    }
    
    if featured_image_id:
        post_data['featured_media'] = featured_image_id
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"尝试创建文章，第 {attempt}/{max_retries} 次")
            response = session.post(
                f"{wp_url}/posts",
                json=post_data,
                timeout=30
            )
            
            logger.info(f"创建文章响应状态码: {response.status_code}")
            
            if response.status_code in [200, 201]:
                try:
                    response_data = response.json()
                    if 'id' in response_data:
                        post_id = response_data['id']
                        
                        # 验证post_id有效性
                        if not post_id or post_id <= 0:
                            logger.error(f"创建文章响应返回无效的ID: {post_id}")
                            continue
                            
                        logger.info(f"草稿创建成功，文章ID: {post_id}")
                        
                        # 检查文章URL
                        if 'link' in response_data:
                            logger.info(f"文章URL: {response_data['link']}")
                        
                        # 记录文章创建
                        db_manager.record_post_creation(title, post_id)
                        
                        # 附加媒体到文章
                        if attached_media_ids:
                            attach_media_to_post(post_id, attached_media_ids, db_manager)
                        
                        # 验证文章实际创建成功
                        try:
                            verify_response = session.get(f"{wp_url}/posts/{post_id}", timeout=10)
                            if verify_response.status_code == 200:
                                logger.info("文章创建验证成功")
                            else:
                                logger.warning(f"文章创建验证返回非200状态码: {verify_response.status_code}")
                        except Exception as e:
                            logger.warning(f"验证文章创建时出错: {str(e)}")
                        
                        return post_id
                    else:
                        logger.error("创建文章响应中没有找到ID字段")
                except json.JSONDecodeError:
                    logger.error("创建文章响应不是有效的JSON格式")
                    logger.error(f"响应内容: {response.text[:150]}...")
            else:
                logger.error(f"创建文章失败: {response.status_code}")
                logger.error(f"响应内容: {response.text[:150]}...")
                
            # 如果不是最后一次尝试，则等待后重试
            if attempt < max_retries:
                wait_time = 3 * attempt
                logger.info(f"等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
                
        except Exception as e:
            logger.error(f"创建文章请求异常: {str(e)}")
            if attempt < max_retries:
                wait_time = 3 * attempt
                logger.info(f"等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
    
    # 所有尝试都失败，记录失败信息
    error_msg = f"创建文章失败: {title} - 尝试 {max_retries} 次后失败"
    db_manager.record_error("create_post", title, error_msg)
    
    logger.error(f"所有尝试均失败，已记录到数据库")
    return None


def attach_media_to_post(post_id, media_ids, db_manager, max_retries=3):
    """将媒体附加到文章"""
    logger.info(f"开始附加 {len(media_ids)} 个媒体到文章 {post_id}")
    
    success_count = 0
    failed_count = 0
    
    for media_id in media_ids:
        if not media_id:
            continue
            
        # 准备附加数据
        attach_data = {
            'post': post_id
        }
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(f"附加媒体 {media_id} 到文章 {post_id}，第 {attempt}/{max_retries} 次")
                
                # 更新媒体的post字段
                response = session.post(
                    f"{wp_url}/media/{media_id}",
                    json=attach_data,
                    timeout=15
                )
                
                if response.status_code in [200, 201]:
                    logger.debug(f"成功附加媒体 {media_id} 到文章 {post_id}")
                    success_count += 1
                    break
                else:
                    logger.warning(f"附加媒体 {media_id} 失败: {response.status_code}")
                    if attempt == max_retries:
                        failed_count += 1
                        db_manager.record_error("attach_media", f"post_{post_id}_media_{media_id}", 
                                              f"HTTP错误: {response.status_code}")
                        
            except Exception as e:
                logger.error(f"附加媒体 {media_id} 时出错: {str(e)}")
                if attempt == max_retries:
                    failed_count += 1
                    db_manager.record_error("attach_media", f"post_{post_id}_media_{media_id}", str(e))
            
            # 重试间隔
            if attempt < max_retries:
                time.sleep(1)
    
    logger.info(f"媒体附加完成 - 成功: {success_count}, 失败: {failed_count}")
    return success_count, failed_count

def verify_media_exists(media_id):
    """验证媒体项是否实际存在于WordPress中"""
    try:
        response = session.get(f"{wp_url}/media/{media_id}", timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"验证媒体项时出错 {media_id}: {str(e)}")
        return False

    
def natural_sort_key(s):
    """自然排序键函数"""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

def get_media_info_batch(media_ids, db_manager):
    """批量获取媒体信息"""
    logger.info(f"批量获取 {len(media_ids)} 个媒体项信息")
    media_info = {}
    batch_size = 20
    
    # 将媒体ID转换为列表
    media_id_list = list(set(media_ids.values()))
    
    for i in range(0, len(media_id_list), batch_size):
        batch = media_id_list[i:i+batch_size]
        logger.debug(f"处理媒体信息批次 {i//batch_size + 1}/{(len(media_id_list)+batch_size-1)//batch_size}")
        
        # 构建查询参数
        include_param = ",".join(map(str, batch))
        max_retries = 3
        success = False
        
        for retry in range(max_retries):
            try:
                response = session.get(
                    f"{wp_url}/media?include={include_param}&per_page={batch_size}",
                    timeout=15
                )
                
                if response.status_code == 200:
                    try:
                        items = response.json()
                        if not isinstance(items, list):
                            logger.error(f"获取媒体信息返回非列表数据: {type(items)}")
                            continue
                            
                        for item in items:
                            media_id = item.get('id')
                            source_url = item.get('source_url')
                            if not media_id or not source_url:
                                logger.warning(f"媒体项缺少ID或URL: {item}")
                                continue
                                
                            # 确保URL是一个有效的字符串
                            if not isinstance(source_url, str) or not source_url.startswith('http'):
                                logger.warning(f"媒体 {media_id} 的URL无效: {source_url}")
                                continue
                                
                            media_info[media_id] = {'url': source_url}
                            
                            # 获取媒体项的其他有用信息
                            if 'media_details' in item and 'sizes' in item['media_details']:
                                sizes = item['media_details']['sizes']
                                # 优先使用large尺寸
                                if 'large' in sizes and 'source_url' in sizes['large']:
                                    media_info[media_id]['large_url'] = sizes['large']['source_url']
                            
                            logger.debug(f"获取到媒体 {media_id} 的URL")
                        
                        success = True
                        break  # 成功获取，跳出重试循环
                    except json.JSONDecodeError as e:
                        logger.error(f"解析媒体信息响应失败: {str(e)}")
                        if retry == max_retries - 1:
                            db_manager.record_error("get_media_info", str(batch), f"JSON解析错误: {str(e)}")
                else:
                    logger.error(f"获取媒体信息失败: {response.status_code}, 响应: {response.text[:100]}")
                    if retry == max_retries - 1:
                        db_manager.record_error("get_media_info", str(batch), f"HTTP错误: {response.status_code}")
            except Exception as e:
                logger.error(f"批量获取媒体信息时出错: {str(e)}")
                if retry == max_retries - 1:
                    db_manager.record_error("get_media_info", str(batch), str(e))
            
            # 如果不是最后一次尝试，则等待后重试
            if not success and retry < max_retries - 1:
                wait_time = (retry + 1) * 2
                logger.warning(f"获取媒体信息失败，等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
    
    # 将媒体ID与文件路径关联
    for file_path, media_id in media_ids.items():
        if media_id in media_info:
            media_info[media_id]['file_path'] = file_path
    
    logger.info(f"成功获取 {len(media_info)} 个媒体项信息")
    return media_info

def process_folder(folder_path, db_manager):
    """处理一个文件夹 - 2025-06-07 防裂图优化版本 + 媒体附加"""
    folder_name = os.path.basename(folder_path)
    logger.info(f"\n{'='*60}")
    logger.info(f"处理文件夹: {folder_name}")
    logger.info(f"{'='*60}")
    
    # 检查是否已创建文章
    existing_post_id = db_manager.is_post_created(folder_name)
    if existing_post_id:
        logger.info(f"✓ 文件夹 {folder_name} 已创建文章，ID: {existing_post_id}")
        return
    
    # 获取图片文件
    try:
        image_files = [f for f in os.listdir(folder_path) 
                      if f.lower().endswith(('.jpg', '.jpeg', '.webp'))]
        image_files.sort(key=natural_sort_key)
    except Exception as e:
        logger.error(f"读取文件夹内容时出错 {folder_path}: {str(e)}")
        db_manager.record_error("process_folder", folder_path, str(e))
        return
    
    if not image_files:
        logger.warning(f"在文件夹 {folder_name} 中没有找到JPG或WEBP文件")
        return
    
    logger.info(f"找到 {len(image_files)} 个图片文件")
    
    # 构建文件路径列表
    file_paths = [os.path.join(folder_path, image_file) for image_file in image_files]
    
    media_ids_dict, failed_uploads = batch_upload_media_concurrent(file_paths, db_manager)
    
    if not media_ids_dict:
        logger.error(f"✗ 文件夹 {folder_name} 中没有图片上传成功")
        db_manager.record_error("process_folder", folder_path, "没有图片上传成功")
        return
    
    # 【防裂图】三重验证机制
    successful_paths = [path for path in file_paths if path in media_ids_dict and media_ids_dict[path]]
    
    if not successful_paths:
        logger.error(f"✗ 文件夹 {folder_name} 中所有图片都上传失败")
        db_manager.record_error("process_folder", folder_path, "所有图片上传失败")
        return
    
    # 【新增】最终验证所有媒体项可访问性
    logger.info("执行最终媒体验证...")
    verified_paths = []
    verification_failed = []
    
    for file_path in successful_paths:
        media_id = media_ids_dict[file_path]
        if verify_media_accessible_concurrent(media_id, session):
            verified_paths.append(file_path)
        else:
            verification_failed.append(file_path)
            logger.warning(f"最终验证失败: {os.path.basename(file_path)} (ID: {media_id})")
    
    if not verified_paths:
        logger.error(f"✗ 文件夹 {folder_name} 中没有通过最终验证的图片")
        db_manager.record_error("process_folder", folder_path, "没有图片通过最终验证")
        return
    
    # 统计信息
    total_files = len(file_paths)
    verified_count = len(verified_paths)
    failed_count = len(failed_uploads) + len(verification_failed)
    
    logger.info(f"文件夹 {folder_name} 处理统计:")
    logger.info(f"  总文件: {total_files}")
    logger.info(f"  验证通过: {verified_count}")
    logger.info(f"  失败: {failed_count}")
    logger.info(f"  成功率: {verified_count/total_files:.1%}")
    
    # 获取媒体信息并构建文章内容
    media_info = get_media_info_batch(media_ids_dict, db_manager)
    
    # 构建文章内容 - 只使用验证通过的图片，增强alt属性
    content = ""
    content_images = 0
    verified_media_ids = []  # 收集所有验证通过的媒体ID
    
    for file_path in verified_paths:
        media_id = media_ids_dict[file_path]
        verified_media_ids.append(media_id)  # 添加到媒体ID列表
        
        # 获取图片URL
        image_url = None
        if media_id in media_info:
            image_url = media_info[media_id].get('large_url') or media_info[media_id].get('url')
        
        if not image_url:
            logger.warning(f"跳过无URL的媒体: {media_id}")
            continue
        
        # 增强的SEO友好alt文本生成
        filename_without_ext = os.path.splitext(os.path.basename(file_path))[0]
        # 保留括号等字符，只移除文件系统不允许的字符
        clean_filename = re.sub(r'[<>:"/\\|?*]', '', filename_without_ext).strip()
        clean_folder = re.sub(r'[<>:"/\\|?*]', '', folder_name).strip()
        
        # 构建更丰富的alt文本，避免重复
        if clean_folder and clean_folder.lower() != clean_filename.lower():
            alt_text = f"{clean_folder} {clean_filename}".strip()
        else:
            alt_text = clean_filename
        
        # 添加图片序号信息，提升SEO效果
        if content_images > 0:
            alt_text = f"{alt_text} 图片{content_images + 1}"
        
        # 限制alt文本长度，SEO最佳实践
        if len(alt_text) > 125:
            alt_text = alt_text[:122] + "..."
        
        # 转义alt文本中的引号，防止JSON格式错误
        escaped_alt_text = alt_text.replace('"', '\\"').replace("'", "\\'")
        
        logger.debug(f"为文章图片生成alt文本: {alt_text}")
        
        # 添加图片块，确保alt属性正确设置，使用转义后的文本
        content += f'<!-- wp:image {{"id":{media_id},"sizeSlug":"large","linkDestination":"none","alt":"{escaped_alt_text}"}} -->\n'
        content += f'<figure class="wp-block-image size-large"><img src="{image_url}" alt="{alt_text}" class="wp-image-{media_id}" title="{clean_filename}" /></figure>\n'
        content += f'<!-- /wp:image -->\n\n'
        
        content_images += 1
    
    if content_images == 0:
        logger.error(f"✗ 文件夹 {folder_name} 中没有有效的图片可添加到文章")
        db_manager.record_error("process_folder", folder_path, "无有效图片可添加到文章")
        return
    
    # 选择特色图片（优先选择横向图片）
    horizontal_images = [path for path in verified_paths if is_horizontal_image(path)]
    featured_image_id = None
    
    if horizontal_images:
        featured_image_id = media_ids_dict[horizontal_images[-1]]
        logger.info(f"选择横向特色图片: {os.path.basename(horizontal_images[-1])}")
    elif verified_paths:
        featured_image_id = media_ids_dict[verified_paths[-1]]
        logger.info(f"选择最后一张作为特色图片: {os.path.basename(verified_paths[-1])}")
    
    # 创建草稿文章并附加所有媒体
    post_id = create_draft_post(folder_name, content, db_manager, featured_image_id, verified_media_ids)
    
    if post_id:
        success_msg = f"✓ 为文件夹 {folder_name} 成功创建草稿文章，ID: {post_id}"
        success_msg += f" (包含 {content_images} 张图片，附加 {len(verified_media_ids)} 个媒体)"
        if failed_count > 0:
            success_msg += f"，{failed_count} 张图片处理失败"
        logger.info(success_msg)
    else:
        logger.error(f"✗ 为文件夹 {folder_name} 创建草稿文章失败")

# 新增失败图片重试功能 - 2025-06-07
def retry_failed_images(db_manager):
    """重试失败的图片上传"""
    logger.info("开始重试失败的图片上传...")
    
    conn = db_manager.get_connection()
    cursor = conn.cursor()
    
    # 获取上传失败的图片
    cursor.execute("""
        SELECT DISTINCT item_path 
        FROM error_logs 
        WHERE operation_type = 'upload_media' 
        AND item_path NOT IN (SELECT file_path FROM uploaded_images)
        ORDER BY error_time DESC
        LIMIT 50
    """)
    failed_images = [row['item_path'] for row in cursor.fetchall()]
    conn.close()
    
    if not failed_images:
        logger.info("没有发现需要重试的失败图片")
        return
    
    logger.info(f"发现 {len(failed_images)} 个失败图片，开始重试...")
    
    # 过滤存在的文件
    existing_files = [img for img in failed_images if os.path.exists(img)]
    
    if not existing_files:
        logger.warning("所有失败的图片文件都不存在了")
        return
    
    logger.info(f"重试 {len(existing_files)} 个存在的图片文件")
    
    # 重新上传
    media_ids, still_failed = batch_upload_media_concurrent(existing_files, db_manager)
    
    logger.info(f"重试完成 - 成功: {len(media_ids)}, 仍然失败: {len(still_failed)}")
    
    if still_failed:
        logger.warning("以下图片重试后仍然失败:")
        for failed_path in still_failed:
            logger.warning(f"  - {os.path.basename(failed_path)}")

            
def process_all_folders(db_manager):
    """处理基础目录下的所有文件夹"""
    logger.info(f"开始处理基础目录: {base_directory}")
    
    # 检查基础目录是否存在
    if not os.path.isdir(base_directory):
        logger.error(f"错误: 基础目录 {base_directory} 不存在")
        return
    
    # 获取所有子文件夹
    try:
        folders = [f for f in os.listdir(base_directory) if os.path.isdir(os.path.join(base_directory, f))]
    except Exception as e:
        logger.error(f"读取基础目录内容时出错: {str(e)}")
        return
    
    logger.info(f"找到 {len(folders)} 个子文件夹")
    
    # 按文件夹名称排序
    folders.sort(key=natural_sort_key)
    
    # 使用tqdm创建进度条
    for folder in tqdm(folders, desc="处理文件夹"):
        full_path = os.path.join(base_directory, folder)
        process_folder(full_path, db_manager)
        
        # 每个文件夹处理完后添加小延迟
        time.sleep(0.5)
    
    logger.info("所有文件夹处理完成")

def retry_failed_folders(db_manager):
    """重试之前失败的文件夹"""
    logger.info("开始重试失败的文件夹...")
    
    failed_folders = db_manager.get_failed_folders()
    
    if not failed_folders:
        logger.info("没有发现失败的文件夹")
        return
    
    logger.info(f"发现 {len(failed_folders)} 个失败的文件夹")
    
    # 按文件夹名称排序
    failed_folders.sort(key=natural_sort_key)
    
    # 处理失败的文件夹
    for folder_name in tqdm(failed_folders, desc="重试失败文件夹"):
        full_path = os.path.join(base_directory, folder_name)
        
        # 检查文件夹是否存在
        if not os.path.isdir(full_path):
            logger.warning(f"文件夹不存在: {folder_name}")
            continue
        
        logger.info(f"重试处理文件夹: {folder_name}")
        process_folder(full_path, db_manager)
        
        # 每个文件夹处理完后添加小延迟
        time.sleep(0.5)
    
    logger.info("所有失败文件夹重试完成")

def check_and_update_existing_media_alt_text(db_manager, limit=100):
    """检查并更新已存在媒体的alt文本"""
    logger.info("开始检查数据库中已上传媒体的alt文本...")
    
    conn = db_manager.get_connection()
    cursor = conn.cursor()
    
    # 获取已上传的媒体记录
    cursor.execute("""
        SELECT file_path, media_id 
        FROM uploaded_images 
        WHERE media_id IS NOT NULL 
        ORDER BY upload_time DESC 
        LIMIT ?
    """, (limit,))
    
    uploaded_media = cursor.fetchall()
    conn.close()
    
    if not uploaded_media:
        logger.info("数据库中没有找到已上传的媒体记录")
        return
    
    logger.info(f"检查最近上传的 {len(uploaded_media)} 个媒体项的alt文本...")
    
    media_to_update = []
    
    # 检查每个媒体项的alt文本
    for record in tqdm(uploaded_media, desc="检查alt文本"):
        file_path = record['file_path']
        media_id = record['media_id']
        
        try:
            # 获取媒体信息
            response = session.get(f"{wp_url}/media/{media_id}", timeout=10)
            
            if response.status_code == 200:
                media_data = response.json()
                current_alt = media_data.get('alt_text', '').strip()
                
                # 如果没有alt文本或alt文本为空，添加到更新列表
                if not current_alt:
                    media_to_update.append((file_path, media_id))
                    logger.debug(f"媒体 {media_id} 缺失alt文本")
                else:
                    logger.debug(f"媒体 {media_id} 已有alt文本: {current_alt}")
            else:
                logger.warning(f"无法获取媒体 {media_id} 信息: {response.status_code}")
                
        except Exception as e:
            logger.error(f"检查媒体 {media_id} 时出错: {str(e)}")
    
    if not media_to_update:
        logger.info("所有检查的媒体都已有alt文本")
        return
    
    logger.info(f"发现 {len(media_to_update)} 个媒体缺失alt文本，开始更新...")
    
    # 批量更新alt文本
    success_count = 0
    failed_count = 0
    
    for file_path, media_id in tqdm(media_to_update, desc="更新alt文本"):
        if update_media_alt_text(media_id, file_path):
            success_count += 1
        else:
            failed_count += 1
            db_manager.record_error("update_alt_text", file_path, f"更新媒体{media_id}的alt文本失败")
    
    logger.info(f"alt文本更新完成 - 成功: {success_count}, 失败: {failed_count}")
    
    if failed_count > 0:
        logger.info("建议使用独立的'检查修复媒体ALT文本.py'工具进行更全面的检查和修复")
    
    return success_count, failed_count

# 简化的主函数 - 2025-06-07 修改
def main():
    """主函数 - 简化连接测试"""
    logger.info("="*50)
    logger.info("WordPress图片上传工具启动")
    logger.info("="*50)
    
    # 初始化数据库管理器
    db_manager = DatabaseManager(db_path)
    
    # 【简化】只进行必要的连接测试 - 2025-06-07
    if not test_wp_connection():
        logger.error("WordPress连接失败，程序终止")
        return
    
    logger.info("连接测试通过，开始处理文件...")
    
    # 处理命令行参数
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == "retry":
            retry_failed_folders(db_manager)
        elif command == "retry-images":  # 重试失败图片命令
            retry_failed_images(db_manager)
        elif command == "check-alt":  # 【新增】检查alt文本命令
            logger.info("检查已上传媒体的alt文本...")
            check_and_update_existing_media_alt_text(db_manager)
        elif command == "folder" and len(sys.argv) > 2:
            folder_name = sys.argv[2]
            full_path = os.path.join(base_directory, folder_name)
            
            if os.path.isdir(full_path):
                process_folder(full_path, db_manager)
            else:
                logger.error(f"文件夹不存在: {folder_name}")
        else:
            logger.info("可用命令:")
            logger.info("  retry - 重试失败的文件夹")
            logger.info("  retry-images - 重试失败的图片")
            logger.info("  check-alt - 检查并修复已上传媒体的alt文本")
            logger.info("  folder <文件夹名> - 处理单个文件夹")
            logger.info("\n提示: 使用独立的'检查修复媒体ALT文本.py'工具可以更全面地检查和修复alt文本")
    else:
        process_all_folders(db_manager)
    
    logger.info("程序执行完毕")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序执行时发生未处理的异常: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
