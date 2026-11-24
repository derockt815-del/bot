"""
Telegram-бот «Бюджет без родителей».
Помогает первокурсникам, переехавшим в другой город, спланировать бюджет.
Методология: правило 80/10/10, недельный конверт, чёрный список трат,
список покупок, дневник расходов.
Автор методологии: Чернышов М.А.

Запуск:
    export BOT_TOKEN="123456:ABC..."     (Windows: set BOT_TOKEN=...)
    python bot.py
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

router = Router()


# ======================================================================
# 1. СОСТОЯНИЯ (FSM)
# ======================================================================
class BudgetForm(StatesGroup):
    income = State()          # доход в месяц
    rent = State()            # аренда
    utilities_included = State()  # коммуналка и интернет в аренде?
    utilities = State()       # сумма коммуналки (если не входит)
    transport = State()       # проезд


# ======================================================================
# 2. ДАННЫЕ ИЗ МЕТОДОЛОГИИ (легко править под свой город)
# ======================================================================
# Примерные цены на продукты на неделю (₽). Меняй под свои магазины.
SHOPPING_LIST = [
    ("Гречка, 1 кг", 110),
    ("Рис, 1 кг", 100),
    ("Макароны, 1 кг", 90),
    ("Куриное филе/бёдра, 1,5 кг", 420),
    ("Яйца, 10 шт.", 110),
    ("Овощи (картофель, морковь, лук, капуста)", 250),
    ("Хлеб, 2 шт.", 80),
    ("Молоко/кефир, 2 л", 140),
    ("Масло, чай, соль, специи (по мере нужды)", 100),
]

BLACKLIST_TEXT = (
    "🚫 <b>Чёрный список трат</b>\n\n"
    "Это то, что незаметно съедает бюджет:\n"
    "• 🛵 Доставка еды — наценка и комиссия делают обед в 1,5–2 раза дороже\n"
    "• 🚕 Такси — вместо него автобус, трамвай или пешком\n"
    "• 🛍 Маркетплейсы — «добавил в корзину» ≠ «надо купить»\n"
    "• 🎲 Спонтанные покупки\n\n"
    "⏳ <b>Правило 24 часов:</b> захотелось вещь, которой нет в плане? "
    "Запиши её и подожди сутки. Если завтра всё ещё нужна — покупай из "
    "«желаемого». Чаще всего желание уходит.\n\n"
    "🔍 <b>Проверь подписки:</b> зайди в банк и в магазины приложений, "
    "посмотри, за что списываются деньги каждый месяц. Лишнее — отключай.\n\n"
    "💡 Ходи в магазин по списку и не на голодный желудок."
)

DIARY_TEXT = (
    "📊 <b>Дневник расходов</b>\n\n"
    "Зачем: чтобы видеть, куда уходят деньги, и не удивляться пустому счёту.\n\n"
    "Как вести (2 минуты в день):\n"
    "1️⃣ Вечером открой заметки или таблицу.\n"
    "2️⃣ Запиши каждую трату: дата — сумма — на что.\n"
    "3️⃣ Отметь категорию: еда, проезд, связь, быт, желаемое.\n"
    "4️⃣ Раз в неделю подсчитай итог и сравни с недельным лимитом.\n"
    "5️⃣ Если вышел за лимит — уменьши траты на следующей неделе.\n\n"
    "Чеки и уведомления банка помогут ничего не забыть. "
    "Главное — честно записывать всё, даже мелочи ✍️"
)

EMERGENCY_TEXT = (
    "🆘 <b>Деньги кончаются раньше срока</b>\n\n"
    "Без паники, такое бывает у всех. Действуй по шагам:\n\n"
    "1️⃣ <b>Убери «желаемое».</b> Кафе, кино, покупки для удовольствия — "
    "пока на паузу.\n"
    "2️⃣ <b>Перейди на простое меню:</b> крупы, макароны, яйца, овощи, "
    "курица. Готовь дома.\n"
    "3️⃣ <b>Сократи лишние поездки</b> и проверь, нет ли ненужных подписок.\n"
    "4️⃣ <b>Только потом трогай резерв</b> — и только на необходимое.\n"
    "5️⃣ Не получается дотянуть? Честно поговори с родителями — "
    "просить помощь нормально.\n\n"
    "А когда придут деньги — пополни резерв первым делом 💪"
)


# ======================================================================
# 3. КЛАВИАТУРЫ
# ======================================================================
def yes_no_kb():
    """Кнопки Да / Нет для вопроса про коммуналку."""
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, входят", callback_data="util_yes")
    kb.button(text="❌ Нет, отдельно", callback_data="util_no")
    kb.adjust(2)
    return kb.as_markup()


def menu_kb():
    """Главное меню после расчёта."""
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Список покупок на неделю", callback_data="shopping")
    kb.button(text="🚫 Чёрный список трат", callback_data="blacklist")
    kb.button(text="📊 Дневник расходов", callback_data="diary")
    kb.button(text="🆘 Деньги кончаются раньше срока", callback_data="emergency")
    kb.button(text="🔄 Пересчитать бюджет", callback_data="recalc")
    kb.adjust(1)
    return kb.as_markup()


def back_kb():
    """Кнопка возврата в меню."""
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В меню", callback_data="menu")
    return kb.as_markup()


# ======================================================================
# 4. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ======================================================================
def parse_number(text: str):
    """Переводит '15 000', '15000₽', '15 000,50' в число. Не вышло — None."""
    cleaned = (
        text.replace("₽", "").replace("руб", "").replace("р.", "")
        .replace(" ", "").replace("\u00a0", "").replace(",", ".").strip()
    )
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value < 0 or value != value:  # отрицательные и NaN не берём
        return None
    return value


def rub(value: float) -> str:
    """Форматирует сумму: 15000 -> '15 000 ₽'."""
    return f"{round(value):,}".replace(",", " ") + " ₽"


def calculate(data: dict) -> dict:
    """Считает бюджет по правилу 80/10/10 и недельный конверт."""
    income = data["income"]
    rent = data["rent"]
    utilities = 0 if data["utilities_included"] else data.get("utilities", 0)
    transport = data["transport"]

    mandatory_limit = income * 0.80   # 80% — обязательные траты
    wishes = income * 0.10            # 10% — желаемое
    reserve = income * 0.10           # 10% — резерв

    fixed = rent + utilities + transport  # то, что уходит точно
    # Остаток обязательных на еду, связь и быт
    rest_mandatory = mandatory_limit - fixed

    # Недельный лимит по формуле проекта:
    # (Сумма - Аренда(+коммуналка) - Резерв - Проезд) / 4
    weekly = (income - rent - utilities - reserve - transport) / 4

    return {
        "income": income, "rent": rent, "utilities": utilities,
        "transport": transport, "mandatory_limit": mandatory_limit,
        "wishes": wishes, "reserve": reserve, "fixed": fixed,
        "rest_mandatory": rest_mandatory, "weekly": weekly,
    }


def build_table(r: dict) -> str:
    """Собирает красивую таблицу в HTML (моноширинный блок <pre>)."""
    rows = [
        ("💰 Доход", rub(r["income"])),
        ("🏠 Аренда", rub(r["rent"])),
    ]
    if r["utilities"]:
        rows.append(("💡 Коммуналка+интернет", rub(r["utilities"])))
    rows += [
        ("🚌 Проезд", rub(r["transport"])),
        ("🍲 Еда/связь/быт", rub(max(r["rest_mandatory"], 0))),
        ("—" * 6, "—" * 8),
        ("📌 Обязательные 80%", rub(r["mandatory_limit"])),
        ("🎁 Желаемое 10%", rub(r["wishes"])),
        ("🛟 Резерв 10%", rub(r["reserve"])),
    ]
    width = max(len(name) for name, _ in rows)
    lines = [f"{name.ljust(width)}  {value.rjust(10)}" for name, value in rows]
    return "<pre>" + "\n".join(lines) + "</pre>"


def build_result_text(r: dict) -> str:
    """Итоговое сообщение с таблицей и недельным конвертом."""
    text = "📊 <b>Твой бюджет на месяц</b>\n\n" + build_table(r) + "\n"

    if r["rest_mandatory"] < 0:
        text += (
            "\n⚠️ <b>Внимание:</b> аренда и проезд уже больше 80% дохода — "
            f"не хватает {rub(-r['rest_mandatory'])} на еду, связь и быт. "
            "Подумай, как сократить расходы или найти подработку, "
            "а пока можно временно уменьшить «желаемое».\n"
        )
    if r["weekly"] <= 0:
        text += (
            "\n😟 На недельный конверт денег почти не остаётся. "
            "Загляни в раздел «🆘 Деньги кончаются раньше срока».\n"
        )
    else:
        text += (
            "\n✉️ <b>Недельный конверт</b>\n"
            "Формула: (доход − жильё − резерв − проезд) ÷ 4\n"
            f"👉 <b>{rub(r['weekly'])} в неделю</b> на еду, связь, быт "
            "и желаемое.\n"
            "Разложи деньги на 4 части — и тратить из одной недели "
            "на другую не придётся."
        )
    return text


# ======================================================================
# 5. ХЭНДЛЕРЫ: СТАРТ И СБОР ДАННЫХ
# ======================================================================
async def ask_income(message: Message, state: FSMContext):
    await state.set_state(BudgetForm.income)
    await message.answer("💰 Сколько денег ты получаешь в месяц? "
                         "(напиши числом, например: 30000)")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! 👋 Я помогу тебе разобраться с деньгами на новом месте.\n\n"
        "Задам несколько простых вопросов, а потом посчитаю бюджет по "
        "правилу <b>80/10/10</b>:\n"
        "• 80% — обязательные траты 🏠\n"
        "• 10% — желаемое 🎁\n"
        "• 10% — резерв 🛟\n\n"
        "Ещё дам недельный лимит, список покупок и советы. "
        "Это бот по проекту «Бюджет без родителей». Поехали! 🚀"
    )
    await ask_income(message, state)


@router.message(BudgetForm.income)
async def get_income(message: Message, state: FSMContext):
    value = parse_number(message.text or "")
    if value is None or value == 0:
        await message.answer("🙏 Мне нужна цифра, например: 30000. "
                             "Попробуй ещё раз!")
        return
    await state.update_data(income=value)
    await state.set_state(BudgetForm.rent)
    await message.answer("🏠 Сколько уходит на аренду жилья в месяц? "
                         "(если живёшь в общежитии — напиши сумму оплаты "
                         "или 0)")


@router.message(BudgetForm.rent)
async def get_rent(message: Message, state: FSMContext):
    value = parse_number(message.text or "")
    if value is None:
        await message.answer("🙏 Напиши, пожалуйста, цифрой, например: 11000.")
        return
    await state.update_data(rent=value)
    await state.set_state(BudgetForm.utilities_included)
    await message.answer("💡 Входят ли коммуналка и интернет в аренду?",
                         reply_markup=yes_no_kb())


@router.message(BudgetForm.utilities_included)
async def utilities_text_instead_of_button(message: Message):
    # Защита от дурака: ждём нажатие кнопки
    await message.answer("👆 Нажми, пожалуйста, одну из кнопок выше: "
                         "«Да» или «Нет».")


@router.callback_query(BudgetForm.utilities_included, F.data == "util_yes")
async def util_yes(call: CallbackQuery, state: FSMContext):
    await state.update_data(utilities_included=True, utilities=0)
    await state.set_state(BudgetForm.transport)
    await call.message.answer("🚌 Сколько тратишь на проезд в месяц?")
    await call.answer()


@router.callback_query(BudgetForm.utilities_included, F.data == "util_no")
async def util_no(call: CallbackQuery, state: FSMContext):
    await state.update_data(utilities_included=False)
    await state.set_state(BudgetForm.utilities)
    await call.message.answer("💡 Сколько платишь за коммуналку и интернет "
                              "в месяц?")
    await call.answer()


@router.message(BudgetForm.utilities)
async def get_utilities(message: Message, state: FSMContext):
    value = parse_number(message.text or "")
    if value is None:
        await message.answer("🙏 Нужна цифра, например: 3000.")
        return
    await state.update_data(utilities=value)
    await state.set_state(BudgetForm.transport)
    await message.answer("🚌 Сколько тратишь на проезд в месяц?")


@router.message(BudgetForm.transport)
async def get_transport(message: Message, state: FSMContext):
    value = parse_number(message.text or "")
    if value is None:
        await message.answer("🙏 Напиши цифрой, например: 1500.")
        return
    await state.update_data(transport=value)
    data = await state.get_data()
    await state.clear()  # сбор закончен, состояние сбрасываем
    result = calculate(data)
    await message.answer(build_result_text(result))
    await message.answer("Что дальше? Выбери 👇", reply_markup=menu_kb())


# ======================================================================
# 6. ХЭНДЛЕРЫ: МЕНЮ С СОВЕТАМИ
# ======================================================================
@router.callback_query(F.data == "shopping")
async def show_shopping(call: CallbackQuery):
    width = max(len(name) for name, _ in SHOPPING_LIST)
    lines = [f"{n.ljust(width)}  {rub(p).rjust(7)}" for n, p in SHOPPING_LIST]
    total = sum(p for _, p in SHOPPING_LIST)
    table = "<pre>" + "\n".join(lines) + "\n" + "—" * (width + 9) + \
        f"\n{'Итого'.ljust(width)}  {rub(total).rjust(7)}</pre>"
    await call.message.answer(
        "📋 <b>Список покупок на неделю</b>\n\n" + table +
        "\nЦены примерные — проверь в своих магазинах. "
        "Бери по списку, лучше в магазинах у дома со скидками 🛒",
        reply_markup=back_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "blacklist")
async def show_blacklist(call: CallbackQuery):
    await call.message.answer(BLACKLIST_TEXT, reply_markup=back_kb())
    await call.answer()


@router.callback_query(F.data == "diary")
async def show_diary(call: CallbackQuery):
    await call.message.answer(DIARY_TEXT, reply_markup=back_kb())
    await call.answer()


@router.callback_query(F.data == "emergency")
async def show_emergency(call: CallbackQuery):
    await call.message.answer(EMERGENCY_TEXT, reply_markup=back_kb())
    await call.answer()


@router.callback_query(F.data == "menu")
async def back_to_menu(call: CallbackQuery):
    await call.message.answer("Что ещё посмотрим? 👇", reply_markup=menu_kb())
    await call.answer()


@router.callback_query(F.data == "recalc")
async def recalc(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await ask_income(call.message, state)


@router.message()
async def fallback(message: Message):
    """Любой другой текст вне диалога."""
    await message.answer("Я пока умею только считать бюджет 🙂 "
                         "Нажми /start, и начнём!")


# ======================================================================
# 7. ЗАПУСК
# ======================================================================
async def main():
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Задай токен: переменная окружения BOT_TOKEN")
    bot = Bot(token=token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b"OK")

def run_health_check():
port = int(os.environ.get("PORT", 10000))
server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
server.serve_forever()

threading.Thread(target=run_health_check, daemon=True).start()

if__name__== "__main__":
    asyncio.run(main())
