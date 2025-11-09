import os
import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'credentials.json'
credentials = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
client = gspread.authorize(credentials)

# Spreadsheet and sheet names
SPREADSHEET_NAME = "Housekeeping"
SHEET_ROOMS = "Номера"
SHEET_MAIDS = "Горничные"
SHEET_LINEN = "Бельё"
SHEET_INVENTORY = "Инвентарь"

# Status constants
STATUS_CLEAN = "Чистый"
STATUS_CHECK = "Проверка"
STATUS_DIRTY = "Грязный"

# User states
class UserState:
    WAITING_ROLE = 1
    WAITING_MAID_NAME = 2
    ASSIGNED_ROOMS = 3

user_states = {}

# Initialize Google Sheet
def init_spreadsheet():
    try:
        spreadsheet = client.open(SPREADSHEET_NAME)
    except gspread.SpreadsheetNotFound:
        # Create new spreadsheet if not exists
        spreadsheet = client.create(SPREADSHEET_NAME)
        
        # Share with service account email (from credentials.json)
        service_account_email = credentials.service_account_email
        spreadsheet.share(service_account_email, perm_type='user', role='writer')
        
        # Create sheets
        spreadsheet.add_worksheet(title=SHEET_ROOMS, rows=100, cols=20)
        spreadsheet.add_worksheet(title=SHEET_MAIDS, rows=100, cols=10)
        spreadsheet.add_worksheet(title=SHEET_LINEN, rows=100, cols=10)
        spreadsheet.add_worksheet(title=SHEET_INVENTORY, rows=100, cols=10)
        
        # Initialize Rooms sheet headers
        rooms_sheet = spreadsheet.worksheet(SHEET_ROOMS)
        rooms_sheet.update('A1:H1', [
            ['№', 'Категория', 'Статус', 'Квартира', 'Горничная', 'Назначено', 'Завершено', 'Проверено']
        ])
        
        # Initialize Maids sheet headers
        maids_sheet = spreadsheet.worksheet(SHEET_MAIDS)
        maids_sheet.update('A1:D1', [
            ['Имя', 'Telegram ID', 'Вход', 'Кол-во номеров']
        ])
        
        # Initialize Linen sheet headers
        linen_sheet = spreadsheet.worksheet(SHEET_LINEN)
        linen_sheet.update('A1:G1', [
            ['Дата', 'Горничная', 'Простыня', 'Пододеяльник', 'Наволочка', 'Полотенце', 'Итого']
        ])
        
        # Initialize Inventory sheet
        inventory_sheet = spreadsheet.worksheet(SHEET_INVENTORY)
        inventory_sheet.update('A1:B1', [
            ['Инвентарь', 'Кол-во на горничную']
        ])
        inventory_sheet.update('A2:B5', [
            ['Тряпки', '2'],
            ['Швабры', '1'],
            ['Совок', '1'],
            ['Ведро', '1']
        ])
    
    return spreadsheet

# Get spreadsheet
spreadsheet = init_spreadsheet()

# Helper functions
def get_maids_sheet():
    return spreadsheet.worksheet(SHEET_MAIDS)

def get_rooms_sheet():
    return spreadsheet.worksheet(SHEET_ROOMS)

def get_linen_sheet():
    return spreadsheet.worksheet(SHEET_LINEN)

def get_inventory_sheet():
    return spreadsheet.worksheet(SHEET_INVENTORY)

def get_user_role(user_id):
    maids_sheet = get_maids_sheet()
    maids = maids_sheet.get_all_records()
    
    for maid in maids:
        if str(maid['Telegram ID']) == str(user_id):
            return 'maid'
    
    # Check if supervisor (for now, just check if not maid)
    # In a real app, you'd have a separate supervisors sheet
    return 'supervisor'

def assign_rooms_to_maid(maid_name, count=18):
    rooms_sheet = get_rooms_sheet()
    rooms = rooms_sheet.get_all_records()
    
    # Find dirty rooms not assigned to anyone
    available_rooms = [
        room for room in rooms 
        if room['Статус'] == STATUS_DIRTY and not room['Горничная']
    ]
    
    if len(available_rooms) < count:
        count = len(available_rooms)
    
    assigned_rooms = available_rooms[:count]
    room_numbers = [room['№'] for room in assigned_rooms]
    
    # Update rooms in sheet
    for room in assigned_rooms:
        row_idx = rooms.index(room) + 2  # +1 for header, +1 for 0-based index
        rooms_sheet.update(f'E{row_idx}', maid_name)
        rooms_sheet.update(f'F{row_idx}', str(datetime.datetime.now()))
        rooms_sheet.update(f'C{row_idx}', STATUS_CHECK)
    
    return room_numbers

def get_maid_inventory():
    inventory_sheet = get_inventory_sheet()
    inventory = inventory_sheet.get_all_records()
    return "\n".join([f"{item['Инвентарь']}: {item['Кол-во на горничную']}" for item in inventory])

# Handlers
@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    
    # Check if user already registered
    maids_sheet = get_maids_sheet()
    maids = maids_sheet.get_all_records()
    
    registered = False
    for maid in maids:
        if str(maid['Telegram ID']) == str(user_id):
            registered = True
            break
    
    if registered:
        role = get_user_role(user_id)
        if role == 'maid':
            # Show maid interface
            rooms = assign_rooms_to_maid(maid['Имя'])
            await message.answer(
                f"Ваши назначенные номера: {', '.join(rooms)}\n\n"
                f"Используйте кнопки ниже для отметки убранных номеров.",
                reply_markup=create_maid_keyboard(rooms)
            )
        else:
            # Show supervisor interface
            await message.answer(
                "Вы вошли как супервайзер. Вы будете получать уведомления о проверке номеров.",
                reply_markup=create_supervisor_keyboard()
            )
    else:
        # New user - ask for role
        user_states[user_id] = UserState.WAITING_ROLE
        await message.answer(
            "👋 Добро пожаловать!\n\nКто вы?",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="🧹 Горничная"), KeyboardButton(text="🧑‍💼 Супервайзер")]
                ],
                resize_keyboard=True
            )
        )

def create_maid_keyboard(room_numbers):
    builder = InlineKeyboardBuilder()
    for room in room_numbers:
        builder.button(text=f"✅ Убрано №{room}", callback_data=f"cleaned_{room}")
    builder.button(text="Сдать бельё", callback_data="linen_report")
    builder.adjust(2)
    return builder.as_markup()

def create_supervisor_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Проверить номера", callback_data="check_rooms")
    return builder.as_markup()

@dp.message(F.text == "🧹 Горничная")
async def maid_role_selected(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_states and user_states[user_id] == UserState.WAITING_ROLE:
        user_states[user_id] = UserState.WAITING_MAID_NAME
        await message.answer(
            "Вы выбрали роль горничной. Пожалуйста, введите ваше имя:",
            reply_markup=ReplyKeyboardRemove()
        )

@dp.message(F.text == "🧑‍💼 Супервайзер")
async def supervisor_role_selected(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_states and user_states[user_id] == UserState.WAITING_ROLE:
        # Register supervisor (for simplicity, we'll just add to maids sheet with a flag)
        maids_sheet = get_maids_sheet()
        maids_sheet.append_row([f"Супервайзер {user_id}", user_id, str(datetime.datetime.now()), "0"])
        
        await message.answer(
            "Вы успешно зарегистрированы как супервайзер. Вы будете получать уведомления о проверке номеров.",
            reply_markup=ReplyKeyboardRemove()
        )
        user_states.pop(user_id, None)

@dp.message(lambda message: message.from_user.id in user_states and user_states[message.from_user.id] == UserState.WAITING_MAID_NAME)
async def maid_name_received(message: types.Message):
    user_id = message.from_user.id
    maid_name = message.text
    
    # Register maid
    maids_sheet = get_maids_sheet()
    maids_sheet.append_row([maid_name, user_id, str(datetime.datetime.now()), "0"])
    
    # Assign rooms
    assigned_rooms = assign_rooms_to_maid(maid_name)
    
    # Get inventory list
    inventory = get_maid_inventory()
    
    await message.answer(
        f"Добро пожаловать, {maid_name}!\n\n"
        f"Ваши назначенные номера: {', '.join(assigned_rooms)}\n\n"
        f"Стандартный инвентарь:\n{inventory}\n\n"
        f"Используйте кнопки ниже для отметки убранных номеров.",
        reply_markup=create_maid_keyboard(assigned_rooms)
    )
    
    user_states.pop(user_id, None)

@dp.callback_query(lambda c: c.data.startswith("cleaned_"))
async def room_cleaned(callback: types.CallbackQuery):
    room_number = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    # Find maid name
    maids_sheet = get_maids_sheet()
    maids = maids_sheet.get_all_records()
    maid_name = None
    for maid in maids:
        if str(maid['Telegram ID']) == str(user_id):
            maid_name = maid['Имя']
            break
    
    if not maid_name:
        await callback.answer("Ошибка: ваши данные не найдены.")
        return
    
    # Update room status
    rooms_sheet = get_rooms_sheet()
    rooms = rooms_sheet.get_all_records()
    
    room_found = False
    for i, room in enumerate(rooms):
        if str(room['№']) == room_number and room['Горничная'] == maid_name:
            row_idx = i + 2
            rooms_sheet.update(f'G{row_idx}', str(datetime.datetime.now()))
            rooms_sheet.update(f'C{row_idx}', STATUS_CHECK)
            room_found = True
            break
    
    if room_found:
        # Notify supervisor
        supervisors = [m for m in maids if m['Имя'].startswith("Супервайзер")]
        for sup in supervisors:
            try:
                await bot.send_message(
                    sup['Telegram ID'],
                    f"Горничная {maid_name} убрала номер №{room_number}. Требуется проверка."
                )
            except:
                pass
        
        await callback.answer(f"Номер {room_number} отмечен как убранный. Ожидается проверка.")
    else:
        await callback.answer("Ошибка: номер не найден или не назначен вам.")

@dp.callback_query(lambda c: c.data == "linen_report")
async def linen_report_start(callback: types.CallbackQuery):
    await callback.message.answer(
        "Пожалуйста, введите количество сданного белья в формате:\n\n"
        "Простыня Пододеяльник Наволочка Полотенце\n\n"
        "Например: 5 3 2 4"
    )
    await callback.answer()

@dp.message(lambda message: message.text.replace(" ", "").isdigit() and len(message.text.split()) == 4)
async def linen_received(message: types.Message):
    user_id = message.from_user.id
    
    # Find maid name
    maids_sheet = get_maids_sheet()
    maids = maids_sheet.get_all_records()
    maid_name = None
    for maid in maids:
        if str(maid['Telegram ID']) == str(user_id):
            maid_name = maid['Имя']
            break
    
    if not maid_name:
        await message.answer("Ошибка: ваши данные не найдены.")
        return
    
    # Parse linen counts
    counts = message.text.split()
    sheet, duvet, pillowcase, towel = counts
    total = sum(int(x) for x in counts)
    
    # Record in sheet
    linen_sheet = get_linen_sheet()
    linen_sheet.append_row([
        str(datetime.datetime.now()),
        maid_name,
        sheet,
        duvet,
        pillowcase,
        towel,
        total
    ])
    
    await message.answer(
        f"Бельё успешно сдано:\n\n"
        f"Простыня: {sheet}\n"
        f"Пододеяльник: {duvet}\n"
        f"Наволочка: {pillowcase}\n"
        f"Полотенце: {towel}\n\n"
        f"Итого: {total} предметов"
    )

@dp.callback_query(lambda c: c.data == "check_rooms")
async def check_rooms(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    # Check if supervisor
    role = get_user_role(user_id)
    if role != 'supervisor':
        await callback.answer("Эта функция доступна только супервайзерам.")
        return
    
    # Get rooms needing check
    rooms_sheet = get_rooms_sheet()
    rooms = rooms_sheet.get_all_records()
    
    rooms_to_check = [room for room in rooms if room['Статус'] == STATUS_CHECK]
    
    if not rooms_to_check:
        await callback.answer("Нет номеров, ожидающих проверки.")
        return
    
    # Create keyboard with rooms to approve
    builder = InlineKeyboardBuilder()
    for room in rooms_to_check:
        builder.button(text=f"🔍 №{room['№']} ({room['Горничная']})", callback_data=f"approve_{room['№']}")
    builder.adjust(2)
    
    await callback.message.answer(
        "Номера, ожидающие проверки:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("approve_"))
async def approve_room(callback: types.CallbackQuery):
    room_number = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    # Check if supervisor
    role = get_user_role(user_id)
    if role != 'supervisor':
        await callback.answer("Эта функция доступна только супервайзерам.")
        return
    
    # Update room status
    rooms_sheet = get_rooms_sheet()
    rooms = rooms_sheet.get_all_records()
    
    for i, room in enumerate(rooms):
        if str(room['№']) == room_number and room['Статус'] == STATUS_CHECK:
            row_idx = i + 2
            rooms_sheet.update(f'H{row_idx}', str(datetime.datetime.now()))
            rooms_sheet.update(f'C{row_idx}', STATUS_CLEAN)
            
            # Notify maid
            maid_name = room['Горничная']
            maids_sheet = get_maids_sheet()
            maids = maids_sheet.get_all_records()
            for maid in maids:
                if maid['Имя'] == maid_name:
                    try:
                        await bot.send_message(
                            maid['Telegram ID'],
                            f"Номер №{room_number} проверен и одобрен супервайзером."
                        )
                    except:
                        pass
                    break
            
            await callback.answer(f"Номер {room_number} отмечен как чистый.")
            return
    
    await callback.answer("Номер не найден или уже проверен.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())