import os
import re
import time
import json
import random
import ctypes
from typing import Dict, Any, List, Optional

from loguru import logger
from dotenv import load_dotenv
from LOLZTEAM.Client import Forum

# Настройка логгера
logger.remove()  # Удаляем стандартный обработчик
logger.add(
    "bot.log",  # Путь к файлу логов
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
    level="INFO",
    rotation="1 day",  # Ротация каждый день
    retention="7 days",  # Хранить логи 7 дней
    encoding='utf-8',  # Добавляем кодировку
    enqueue=True  # Асинхронная запись
)
logger.add(
    lambda msg: print(msg),  # Вывод в консоль
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
    level="INFO",
    colorize=True
)

# Загрузка переменных окружения
load_dotenv()

def set_title():
    if os.name == 'nt':
        ctypes.windll.kernel32.SetConsoleTitleW('Forum Bot')

def call_exit(error: str):
    logger.error(error)
    input('Нажмите Enter для выхода...')
    raise SystemExit()

def load_config() -> Dict[str, Any]:
    """Загрузка конфигурации из переменных окружения"""
    required_vars = ['LOLZ_TOKEN', 'API_DOMAIN', 'THREAD_URL']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        call_exit(f'Ошибка: не заполнены обязательные переменные окружения: {", ".join(missing_vars)}')
    
    return {
        'thread_url': os.getenv('THREAD_URL'),
        'lolz_token': os.getenv('LOLZ_TOKEN'),
        'api_domain': os.getenv('API_DOMAIN'),
        'proxy': os.getenv('PROXY_URL', ''),
        'delay': [
            int(os.getenv('MIN_DELAY', 15)),
            int(os.getenv('MAX_DELAY', 20))
        ],
        'message_days': int(os.getenv('MESSAGE_DAYS', '3')),
        'message_buy_url': os.getenv('MESSAGE_BUY_URL', 'https://zelenka.guru/threads/1234567'),
        'dynamic_data': False
    }

class LolzBot:
    def __init__(self, token: str, config: Dict[str, Any]):
        self.config = config
        self.api = Forum(token=token)
        self.checked_posts = set()  # Множество для хранения проверенных постов
        self.author_id = None  # ID автора темы
        self.last_checked_post_id = 0  # ID последнего проверенного поста
        self.last_checked_page = 1  # Добавляем переменную для последней проверенной страницы
        
        # Загружаем историю ответов
        try:
            with open('replied_users.json', 'r', encoding='utf-8') as f:
                replied_data = json.load(f)
                self.replied_posts = set(replied_data.keys())
                # Находим последний проверенный пост и страницу
                if replied_data:
                    self.last_checked_post_id = max(map(int, replied_data.keys()))
                    self.last_checked_page = replied_data[str(self.last_checked_post_id)]
                    logger.info(f'Последний проверенный пост: {self.last_checked_post_id} на странице {self.last_checked_page}')
        except:
            self.replied_posts = set()
        
        if config['proxy']:
            self.api.settings.proxy = config['proxy']
            
        # Добавляем таймаут для API запросов
        self.api.settings.timeout = 30
        
        # Извлечение ID темы из URL
        match = re.search(r'/threads/(\d+)/?', config['thread_url'])
        if not match:
            call_exit(f'Неверный формат URL темы: {config["thread_url"]}')
        self.thread_id = int(match.group(1))
        
        # Получаем ID автора темы при инициализации
        thread_info = self.api_request('GET', f'/threads/{self.thread_id}')
        if 'thread' not in thread_info:
            call_exit('Не удалось получить информацию о теме')
        self.author_id = thread_info['thread']['creator_user_id']
        logger.info(f'ID автора темы: {self.author_id}')

    def api_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Выполнение API запроса с обработкой ошибок"""
        try:
            response = self.api.request(method, endpoint, **kwargs)
            return response.json()
        except Exception as e:
            logger.error(f'Ошибка API запроса {endpoint}: {str(e)}')
            time.sleep(5)  # Пауза перед повторной попыткой
            return {}

    def check_user(self) -> bool:
        """Проверка валидности токена"""
        try:
            self.api.users.get(user_id=1)
            return True
        except Exception as e:
            logger.error(f'Ошибка проверки токена: {e}')
            return False

    def get_posts(self) -> Optional[List[Dict[str, Any]]]:
        """Получение новых постов из темы"""
        try:
            # Получаем текущий список отвеченных постов
            with open('replied_users.json', 'r', encoding='utf-8') as f:
                sent_messages = json.load(f)
                
            logger.info('Запрашиваю информацию о постах...')
            
            # Получаем первую страницу для определения общего количества страниц
            response_data = self.api_request('GET', f'/posts', params={
                'thread_id': self.thread_id,
                'order': 'post_date',
                'direction': 'asc',  # От старых к новым
                'limit': 20  # Устанавливаем лимит в 20 постов на страницу
            })
            
            if not response_data:
                logger.error('Не удалось получить информацию о постах')
                return []
                
            # Определяем количество страниц
            total_pages = response_data.get('links', {}).get('pages', 1)
            logger.info(f'Найдено {total_pages} страниц(-ы) с постами')
            
            # Собираем все посты
            all_posts = []
            
            # Начинаем с первой страницы и идем до последней
            current_page = 1
                
            logger.info(f'Начинаю проверку с первой страницы')
            
            # Проверяем страницы последовательно
            while current_page <= total_pages:
                logger.info(f'Проверяю страницу {current_page}...')
                
                page_data = self.api_request('GET', f'/posts', params={
                    'thread_id': self.thread_id,
                    'page': current_page,
                    'order': 'post_date',
                    'direction': 'asc',  # От старых к новым
                    'limit': 20
                })
                
                if not page_data or 'posts' not in page_data:
                    logger.error(f'Ошибка при получении страницы {current_page}')
                    break
                    
                posts = page_data['posts']
                logger.info(f'Получено {len(posts)} постов на странице {current_page}')
                
                # Фильтруем посты
                for post in posts:
                    post_id = str(post['post_id'])
                    username = post['poster_username']
                    
                    # Пропускаем посты автора темы
                    if post['poster_user_id'] == self.author_id:
                        continue
                        
                    # Пропускаем уже отвеченные посты
                    if post_id in sent_messages:
                        continue
                    
                    # Проверяем комментарии
                    logger.info(f'➤ Проверяю пост от {username} (ID: {post_id})')
                    comments_data = self.api_request('GET', f'/posts/{post_id}/comments')
                    
                    if not comments_data:
                        logger.error(f'❌ Ошибка получения комментариев для поста {post_id}')
                        continue
                        
                    # Получаем ID бота
                    bot_id = self.api.request('GET', '/users/me').json()['user']['user_id']
                    
                    # Проверяем комментарии
                    comments = comments_data.get('comments', [])
                    total_comments = len(comments)
                    deleted_comments = 0
                    active_comments = 0
                    
                    # Проверяем только активные комментарии
                    has_active_comment = False
                    for comment in comments:
                        comment_user_id = comment.get('poster_user_id')
                        comment_username = comment.get('poster_username', 'Неизвестный')
                        
                        # Проверяем статус комментария
                        is_deleted = comment.get('post_comment_is_deleted', False)
                        is_published = comment.get('post_comment_is_published', True)
                        is_comment_deleted = is_deleted or not is_published
                        
                        if not is_comment_deleted:
                            active_comments += 1
                            has_active_comment = True
                        else:
                            deleted_comments += 1
                    
                    logger.info(f'📊 Статистика: {active_comments} активных, {deleted_comments} удалённых')
                    
                    # Если есть активный комментарий - пропускаем пост
                    if has_active_comment:
                        logger.info(f'⏩ Пропускаю пост - есть активный комментарий')
                        # Сохраняем информацию о проверенном посте
                        with open('replied_users.json', 'r+', encoding='utf-8') as f:
                            sent_messages = json.load(f)
                            sent_messages[str(post_id)] = current_page
                            f.seek(0)
                            json.dump(sent_messages, f, indent=4)
                            f.truncate()
                        continue
                    
                    logger.info(f'✅ Добавляю пост для ответа')
                    
                    # Берем один ключ
                    with open('data.txt', 'r', encoding='utf-8') as f:
                        keys = f.readlines()
                        if not keys:
                            logger.error('Закончились ключи')
                            return []
                        key = keys[0].strip()
                    
                    # Формируем текст с ключом
                    prize = f'[userids={post["poster_user_id"]};align=left][B]Выдал {self.config["message_days"]} дня доступа![/B]\n'
                    prize += f'[URL="{key}"]{key}[/URL]\n'
                    prize += f'[B][I]Если понравится, купить можно тут:\n[URL="{self.config["message_buy_url"]}"]{self.config["message_buy_url"]}[/URL][/I][/B][/userids]'

                    logger.info(f'Подготовлен текст комментария для {username}:\n{prize}')
                    
                    # Отправляем комментарий
                    success = self.post_comment(post['post_id'], username, post['poster_user_id'], prize)
                    
                    if success:
                        logger.info(f'✅ Комментарий для {username} отправлен успешно')
                        
                        # Удаляем использованный ключ
                        keys.pop(0)
                        with open('data.txt', 'w', encoding='utf-8') as f:
                            f.write(''.join(keys))
                        
                        # Сохраняем информацию об отправленном комментарии
                        with open('replied_users.json', 'r+', encoding='utf-8') as f:
                            sent_messages = json.load(f)
                            sent_messages[str(post_id)] = current_page
                            f.seek(0)
                            json.dump(sent_messages, f, indent=4)
                            f.truncate()
                    else:
                        logger.error(f'❌ Не удалось отправить комментарий для {username}')
                        time.sleep(5)
                        continue
                    
                    time.sleep(3)  # Задержка между отправкой комментариев
                
                time.sleep(3)  # Задержка между проверкой страниц
                current_page += 1  # Увеличиваем номер страницы
                self.last_checked_page = current_page  # Сохраняем текущую страницу
            
            return []
            
        except Exception as e:
            logger.error(f'Ошибка при получении постов: {str(e)}')
            return []

    def post_comment(self, post_id: int, username: str, user_id: int, text: str) -> bool:
        """Публикация комментария к посту"""
        try:
            logger.info(f'📝 Отправляю комментарий для {username} (ID: {user_id})')
            logger.info(f'Текст комментария: {text}')
            
            # Отправляем комментарий
            response_data = self.api_request('POST', f'/posts/{post_id}/comments', data={
                'comment_body': text,
                'message_html': text  # Добавляем HTML-версию
            })
            
            logger.info(f'Ответ API: {response_data}')
            
            if response_data and 'comment' in response_data:
                logger.info(f'✅ Комментарий успешно отправлен')
                return True
            
            if response_data and 'errors' in response_data:
                logger.error(f'❌ Ошибка API: {response_data["errors"]}')
            else:
                logger.error(f'❌ Неожиданный ответ API: {response_data}')
            return False
            
        except Exception as e:
            logger.error(f'❌ Ошибка при создании комментария: {str(e)}')
            return False

def distribution(bot: LolzBot, keys: List[str]):
    """Основная функция распределения ключей"""
    try:
        logger.info('Произвожу парсинг...')
        bot.get_posts()  # Теперь эта функция сама отправляет комментарии
        time.sleep(3)  # Небольшая задержка перед следующей проверкой
            
    except Exception as e:
        logger.error(f'Ошибка в distribution: {str(e)}')
        time.sleep(5)

def main():
    try:
        set_title()
        logger.info('Бот запущен')
        
        logger.info('Загружаю конфигурацию...')
        config = load_config()
        logger.info('Конфигурация загружена успешно')
        
        # Загрузка ключей
        logger.info('Начинаю загрузку ключей...')
        try:
            with open('data.txt', 'r', encoding='utf-8') as file:
                keys = file.readlines()
                if not keys:
                    call_exit('Ошибка: файл data.txt пуст')
                logger.info(f'Загружено {len(keys)} ключей')
        except Exception as e:
            logger.error(f'Ошибка при чтении data.txt: {str(e)}')
            call_exit(f'Ошибка при чтении data.txt: {str(e)}')

        # Инициализация бота
        logger.info('Инициализирую бота...')
        bot = LolzBot(config['lolz_token'], config)
        logger.info('Бот инициализирован')
        
        logger.info('Проверяю токен...')
        if not bot.check_user():
            call_exit('Ошибка: неверный токен')
        
        logger.info('Бот успешно авторизован')
        logger.info(f'URL темы: {config["thread_url"]}')
        logger.info('Начинаю проверку сообщений...')

        # Основной цикл
        while True:
            try:
                # Проверяем наличие ключей
                if not keys:
                    logger.info('Перезагружаю список ключей из data.txt...')
                    with open('data.txt', 'r', encoding='utf-8') as file:
                        keys = file.readlines()
                        if not keys:
                            logger.error('Файл data.txt пуст')
                            time.sleep(5)
                            continue
                        logger.info(f'Загружено {len(keys)} ключей')
                
                distribution(bot, keys)
            except Exception as e:
                logger.error(f'Ошибка в основном цикле: {str(e)}')
                time.sleep(random.randrange(*config['delay']))
                
    except Exception as e:
        logger.error(f'Критическая ошибка: {str(e)}')
        call_exit(f'Критическая ошибка: {str(e)}')

if __name__ == '__main__':
    main()
