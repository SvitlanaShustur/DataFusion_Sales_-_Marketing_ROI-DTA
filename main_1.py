import os
import pandas as pd
import numpy as np
import db_sql as db
from sqlalchemy import text
from dotenv import load_dotenv
import matplotlib.pyplot as plt

print("[BOOT] Script started ✅")  # ← якщо цього НЕ видно, ти запускаєш НЕ цей файл

load_dotenv()


def load_marketing_csv(csv_path: str) -> pd.DataFrame:
    print(f"[STEP] Loading marketing CSV: {csv_path}")
    df = pd.read_csv(csv_path)
    df["month"] = pd.to_datetime(df["month"], errors="coerce")
    return df


def clean_marketing_data(df_raw: pd.DataFrame) -> pd.DataFrame:
    print("[STEP] Cleaning marketing data")
    df = df_raw.copy()

    df["channel"] = df["channel"].astype(str).str.strip()

    channel_map = {
        "google ads": "Google Ads",
        "google ad": "Google Ads",
        "googleads": "Google Ads",
        "facebook": "Facebook",
        "instagram": "Instagram",
        "tiktok": "TikTok",
        "youtube": "YouTube",
    }

    df["channel_std"] = (
        df["channel"].str.lower().map(channel_map).fillna(df["channel"].str.title())
    )

    def parse_spend(x):
        if pd.isna(x):
            return np.nan
        x = str(x).strip().lower()
        if x in ["", "na", "missing", "null", "none"]:
            return np.nan
        try:
            return float(x)
        except:
            return np.nan

    df["spend_amount_num"] = df["spend_amount"].apply(parse_spend)
    df["negative_spend_flag"] = df["spend_amount_num"] < 0
    df.loc[df["negative_spend_flag"], "spend_amount_num"] = np.nan

    clean = df[["month", "channel_std", "spend_amount_num", "negative_spend_flag"]].rename(
        columns={"channel_std": "channel", "spend_amount_num": "spend_amount"}
    )

    clean["month"] = pd.to_datetime(clean["month"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    return clean


def agg_sales_monthly(orders_df: pd.DataFrame) -> pd.DataFrame:
    print("[STEP] Aggregating sales monthly")
    df = orders_df.copy()
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    df = df.dropna(subset=["order_date"])

    df["month"] = df["order_date"].dt.to_period("M").dt.to_timestamp()

    monthly_sales = df.groupby("month", as_index=False).agg(
        order_count=("order_id", "count"),
        total_sales=("order_amount", "sum"),
    )
    return monthly_sales


def plot_sales_and_marketing_by_channel(monthly_sales: pd.DataFrame, df_marketing_clean: pd.DataFrame) -> None:
    print("[STEP] Plotting графік...")

    if monthly_sales.empty:
        print("[WARN] monthly_sales порожній -> графік не будується")
        return

    sales = monthly_sales.copy()
    sales["month"] = pd.to_datetime(sales["month"], errors="coerce")
    sales = sales.dropna(subset=["month"]).sort_values("month")

    mkt = df_marketing_clean.copy()
    mkt["month"] = pd.to_datetime(mkt["month"], errors="coerce")
    mkt = mkt.dropna(subset=["month"])
    mkt["spend_amount"] = pd.to_numeric(mkt["spend_amount"], errors="coerce").fillna(0)

    mkt_channel = (
        mkt.groupby(["month", "channel"], as_index=False)
           .agg(marketing_spend=("spend_amount", "sum"))
    )

    mkt_pivot = (
        mkt_channel.pivot(index="month", columns="channel", values="marketing_spend")
                   .fillna(0)
                   .sort_index()
    )

    mkt_pivot = mkt_pivot.reindex(sales["month"]).fillna(0)

    fig, ax1 = plt.subplots(figsize=(12, 6))

    ax1.plot(sales["month"], sales["total_sales"], marker="o", linewidth=2, label="Total Sales")
    ax1.set_xlabel("Month")
    ax1.set_ylabel("Total Sales")
    ax1.grid(True, linestyle="--", linewidth=0.5)

    ax2 = ax1.twinx()
    for ch in mkt_pivot.columns:
        ax2.plot(mkt_pivot.index, mkt_pivot[ch], marker="x", linewidth=1.5, label=f"Marketing: {ch}")
    ax2.set_ylabel("Marketing Spend")

    l1, lab1 = ax1.get_legend_handles_labels()
    l2, lab2 = ax2.get_legend_handles_labels()
    ax1.legend(l1 + l2, lab1 + lab2, loc="upper left")

    plt.title("Sales vs Marketing Spend by Channel (Monthly)")
    plt.xticks(rotation=45)
    plt.tight_layout()

    # ✅ Головне: зберігаємо у файл, навіть якщо show не працює
    out_file = "sales_marketing_plot.png"
    plt.savefig(out_file, dpi=150)
    print(f"[OK] Графік збережено у файл: {out_file}")

    # show іноді не відкриває вікно у VS Code — але файл уже є
    plt.show(block=True)


def main():
    print("[MAIN] main() started ✅")

    # 1) CSV маркетингу
    csv_path = "marketing_spend.csv"
    df_raw = load_marketing_csv(csv_path)
    df_marketing_clean = clean_marketing_data(df_raw)
    print("[INFO] marketing rows:", len(df_marketing_clean))

    # 2) Postgres
    pg_url = os.getenv("POSTGRES_URL")
    if not pg_url:
        print("[ERROR] POSTGRES_URL не знайдено у .env")
        return

    print("[STEP] Connecting to Postgres...")
    pg_engine = db.get_postgres_engine(pg_url)
    print("[OK] Engine created")

    # 3) Виконуємо orders.sql (якщо треба)
    orders_sql_path = "orders.sql"
    if os.path.exists(orders_sql_path):
        print("[STEP] Executing orders.sql")
        try:
            with open(orders_sql_path, "r", encoding="utf-8") as f:
                orders_sql = f.read()

            statements = [s.strip() for s in orders_sql.split(";") if s.strip()]
            with pg_engine.begin() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))
            print("[OK] orders.sql executed")
        except Exception as e:
            print(f"[WARN] orders.sql execution error (може таблиця вже існує): {e}")
    else:
        print("[WARN] orders.sql не знайдено — пропускаю виконання")

    # 4) Завантажуємо orders
    print("[STEP] Loading orders from Postgres...")
    orders = db.load_orders_postgres(pg_engine)
    print("[OK] orders rows:", len(orders))

    if orders.empty:
        print("[ERROR] orders порожній -> нема що аналізувати")
        return

    # 5) Агрегація продажів + графік
    monthly_sales = agg_sales_monthly(orders)
    monthly_sales.to_csv("monthly_sales.csv", index=False)
    print("[OK] monthly_sales saved")

    # ✅ ВАЖЛИВО: виклик графіка
    plot_sales_and_marketing_by_channel(monthly_sales, df_marketing_clean)

    print("[MAIN] main() finished ✅")

df = pd.read_csv("monthly_roi.csv")
print(df.to_string(index=False))


if __name__ == "__main__":
    main()

import pandas as pd

# 1) Завантажуємо файл з маркетингом
df_marketing = pd.read_csv("marketing_spend.csv")

# 2) Приводимо month до datetime
df_marketing["month"] = pd.to_datetime(df_marketing["month"], errors="coerce")

# 3) Приводимо витрати до числового типу
df_marketing["spend_amount"] = pd.to_numeric(
    df_marketing["spend_amount"], errors="coerce"
)

# 4) Нормалізуємо month до початку місяця
df_marketing["month"] = df_marketing["month"].dt.to_period("M").dt.to_timestamp()

# 5) Агрегація по місяцях
monthly_marketing = (
    df_marketing
    .groupby("month", as_index=False)
    .agg(marketing_spend=("spend_amount", "sum"))
)

# 6) Сортуємо по місяцях
monthly_marketing = monthly_marketing.sort_values("month")

# 7) Виводимо таблицю
print(monthly_marketing.to_string(index=False))

# (опційно) зберігаємо у CSV
monthly_marketing.to_csv("monthly_marketing.csv", index=False)