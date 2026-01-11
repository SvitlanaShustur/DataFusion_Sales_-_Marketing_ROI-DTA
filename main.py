import pandas as pd
import numpy as np
import db_sql as db
import os
from dotenv import load_dotenv
load_dotenv()

monthly_category_sgl = '''
SELECT 
    to_char(order_date, 'YYYY-MM') AS month,
    product_category,
    COUNT(order_id) AS count_orders,
    SUM(order_amount) AS sum_amount
FROM orders
GROUP BY month, product_category
ORDER BY month;
'''

monthly_sgl = '''
SELECT 
    product_category,
    DATE_TRUNC('month', order_date) as month,
    count(order_id) as count_orders,
    sum(order_amount) as sum_amount
FROM orders
GROUP BY month, product_category
ORDER BY month;
'''

top_3_custimers_sgl = '''
SELECT 
    customer_id,
    SUM(order_amount) AS sum_customers
FROM orders
GROUP BY customer_id
LIMIT 3
'''



def load_marketing_csv(csv_path: str) -> pd.DataFrame:
    """
    Завантажує CSV-файл у DataFrame та перетворює колонку 'month' у формат datetime.

    Параметри:
    - csv_path: шлях до CSV-файлу.

    Повертає:
    - df: DataFrame з даними маркетингових витрат.
    """
    df = pd.read_csv(csv_path)

    # Перетворюємо колонку 'month' у datetime.
    # errors='coerce' означає:
    # - якщо значення не можна перетворити на дату, воно стане NaT (порожня дата)
    df['month'] = pd.to_datetime(df['month'], errors='coerce')

    return df


def clean_marketing_data(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Очищає та стандартизує маркетингові дані:
    - чистить назви каналів (channel)
    - приводить канали до єдиного стандарту (channel_std)
    - перетворює витрати (spend_amount) у число
    - позначає негативні витрати та замінює їх на NaN
    - залишає лише потрібні колонки та нормалізує month до початку місяця

    Параметри:
    - df_raw: "сирий" DataFrame, як завантажено з CSV.

    Повертає:
    - clean: очищений DataFrame зі стандартними колонками:
      month, channel, spend_amount, negative_spend_flag
    """
    # Робимо копію, щоб не змінювати оригінальний df_raw
    df = df_raw.copy()

    # 1) Чистимо колонку channel:
    # - перетворюємо на str (на випадок, якщо там числа/NaN)
    # - прибираємо пробіли зліва і справа
    df['channel'] = df['channel'].astype(str).str.strip()

    # 2) Мапа стандартних назв каналів:
    # Логіка: ми зведемо різні написання (google ads/googlead/googleads) до одного стандарту.
    channel_map = {
        "google ads": "Google Ads",
        "google ad": "Google Ads",     # ⚠️ у тебе це було двічі (дублікат ключа не потрібен)
        "googleads": "Google Ads",
        "facebook": "Facebook",
        "instagram": "Instagram",
        "tiktok": "TikTok",
        "youtube": "YouTube"           # ⚠️ краще нижнім регістром, бо ти робиш .str.lower()
    }

    # 3) Стандартизація каналів:
    # - df['channel'].str.lower() робить значення нижнім регістром (для надійного match у map)
    # - .map(channel_map) підставляє стандартну назву, якщо ключ є у словнику
    # - .fillna(df["channel"].str.title()) якщо ключа немає, робимо "гарний" формат Title Case
    df['channel_std'] = (
        df['channel']
        .str.lower()
        .map(channel_map)
        .fillna(df["channel"].str.title())
    )

    # Внутрішня функція для парсингу spend_amount
    def parse_spend(x):
        """
        Перетворює значення витрат у float.
        Повертає NaN, якщо значення порожнє/некоректне.

        Обробляє такі випадки:
        - NaN (порожнє)
        - "", "na", "missing", "null", "none"
        - текст замість числа
        """
        # Якщо значення вже NaN -> повертаємо NaN
        if pd.isna(x):
            return np.nan

        # Перетворюємо в строку, прибираємо пробіли, знижуємо регістр
        x = str(x).strip().lower()

        # Якщо значення "порожнє" або явно позначає пропуск
        if x in ["", "na", "missing", "null", "none"]:
            return np.nan

        # Пробуємо перетворити на float.
        # Якщо не вийшло (наприклад "12$" або "ten") -> NaN
        try:
            return float(x)
        except:
            return np.nan

    # 4) Створюємо числову колонку витрат
    df['spend_amount_num'] = df['spend_amount'].apply(parse_spend)

    # 5) Прапорець для негативних витрат:
    # True, якщо spend_amount_num < 0
    # (це може бути помилка в даних або повернення коштів — залежить від бізнес-логіки)
    df['negative_spend_flag'] = df['spend_amount_num'] < 0

    # 6) Якщо витрати негативні — ми замінюємо їх на NaN (викидаємо з аналізу)
    df.loc[df['negative_spend_flag'], 'spend_amount_num'] = np.nan

    # 7) Формуємо "чистий" датафрейм:
    # залишаємо лише потрібні колонки і перейменовуємо їх у стандартний формат
    clean = df[['month', 'channel_std', 'spend_amount_num', 'negative_spend_flag']].rename(
        columns={
            'channel_std': 'channel',
            'spend_amount_num': 'spend_amount'
        }
    )

    # 8) Нормалізуємо month до першого дня місяця (month start)
    # - dt.to_period('M') перетворює дату в період "місяць" (наприклад 2024-03)
    # - dt.to_timestamp() робить timestamp першого дня цього місяця (2024-03-01 00:00:00)
    clean['month'] = pd.to_datetime(clean['month']).dt.to_period('M').dt.to_timestamp()

    return clean


'''
Що ти отримуєш на виході (після clean_marketing_data)
DataFrame з колонками:
month — дата, округлена до початку місяця (наприклад 2023-07-01)
channel — стандартизована назва каналу (Google Ads / Facebook / …)
spend_amount — витрати як число float (або NaN, якщо некоректно)
negative_spend_flag — True, якщо вхідні витрати були < 0
'''
def main():
    # --- Використання функцій ---
    csv_path = "marketing_spend.csv"

    # 1) Завантажуємо сирі дані
    df_raw = load_marketing_csv(csv_path)

    # 2) Чистимо дані
    df = clean_marketing_data(df_raw)

    # 3) Друкуємо результат
    print(df)

    orders_sql_pact = "orders.sql"
    pg_url = os.getenv("POSTGRES_URL")
    pg_engine = db.get_postgres_engine(pg_url)
    orders = db.load_orders_postgres(pg_engine)
    print(orders)


if __name__ == "__main__":
    main()
