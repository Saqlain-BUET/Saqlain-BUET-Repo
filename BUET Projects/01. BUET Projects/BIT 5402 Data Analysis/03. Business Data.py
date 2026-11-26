import os
import re
import pandas as pd
import numpy as np

# 1. File Paths
DATA_DIR = r"C:\Users\saqla\Downloads\lab3_data"
OUTPUT_DIR = os.path.join(DATA_DIR, "cleaned_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

ctg_raw = pd.read_csv(os.path.join(DATA_DIR, "orders_chattogram.csv"))
dhk_raw = pd.read_csv(os.path.join(DATA_DIR, "orders_dhaka.csv"))
syl_raw = pd.read_csv(os.path.join(DATA_DIR, "orders_sylhet.csv"))
prod_raw = pd.read_csv(os.path.join(DATA_DIR, "products.csv"))
ret_raw = pd.read_csv(os.path.join(DATA_DIR, "returns.csv"))

# 2. Cleaning Helper Functions
bn_to_en = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

def clean_order_id(val):
    if pd.isna(val):
        return np.nan
    s = str(val).replace("\xa0", " ").strip().upper()
    m = re.match(r"^([A-Z]+)[\s\-/]+(\d+)$", s)
    return f"{m.group(1)}-{int(m.group(2)):04d}" if m else s

def clean_price(val):
    if pd.isna(val):
        return np.nan
    s = str(val).strip().translate(bn_to_en)
    if s in ["", "-", "nan", "None", "N/A", "null", "NULL"]:
        return np.nan
    s = re.sub(r"[Tt][Kk]\.?|৳|,|\s", "", s)
    if s.lower().endswith("k"):
        try:
            return float(s[:-1]) * 1000
        except ValueError:
            return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan

def clean_qty(val):
    if pd.isna(val):
        return np.nan
    s = re.sub(r"pcs?", "", str(val).strip().translate(bn_to_en), flags=re.IGNORECASE).strip()
    try:
        return float(s)
    except ValueError:
        return np.nan

def clean_phone(val):
    if pd.isna(val):
        return np.nan
    digits = re.sub(r"\D", "", str(val))
    if digits.startswith("88"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("1"):
        digits = "0" + digits
    return digits if re.match(r"^01[3-9]\d{8}$", digits) else np.nan

def clean_sku(val):
    if pd.isna(val):
        return np.nan
    return re.sub(r"^(SK)(\d+)$", r"\1-\2", str(val).strip().upper())

# 3. Clean Products Catalog
prod = prod_raw.copy()
for col in ["sku", "product_name", "category"]:
    prod[col] = prod[col].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
prod["sku"] = prod["sku"].str.upper()
prod["list_price"] = pd.to_numeric(prod["list_price"])
prod["unit_cost"] = pd.to_numeric(prod["unit_cost"])
prod["cost_valid_from"] = pd.to_datetime(prod["cost_valid_from"])
prod = prod.drop_duplicates().sort_values(["sku", "cost_valid_from"]).reset_index(drop=True)

prod_lookup = prod[["sku", "product_name", "category", "list_price"]].drop_duplicates().copy()
prod_lookup["name_key"] = prod_lookup["product_name"].str.lower()

# 4. Clean Each Branch Orders Table
def clean_branch_orders(df, branch_name, id_col, date_col, date_fmt, item_col, is_sku, qty_col, price_col, phone_col):
    d = df.copy()
    d["order_id"] = d[id_col].apply(clean_order_id)
    d["order_date"] = pd.to_datetime(d[date_col], format=date_fmt, errors="coerce")
    d["branch"] = branch_name
    d["quantity"] = d[qty_col].apply(clean_qty)
    d["unit_price"] = d[price_col].apply(clean_price)
    d["customer_phone"] = d[phone_col].apply(clean_phone)

    if is_sku:
        d["sku"] = d[item_col].apply(clean_sku)
        d = d.merge(prod_lookup[["sku", "product_name", "category", "list_price"]], on="sku", how="left")
    else:
        d["name_key"] = d[item_col].astype(str).str.strip().str.replace(r"\s+", " ", regex=True).str.lower()
        d = d.merge(prod_lookup[["sku", "product_name", "category", "list_price", "name_key"]], on="name_key", how="left")

    # Remove exact and disguised duplicates after normalization
    d = d.drop_duplicates(subset=["order_id", "order_date", "branch", "sku", "quantity", "unit_price", "customer_phone"])

    # Drop invalid/dummy products, null/zero/negative quantities or prices, and extreme outliers
    valid_mask = (
        d["sku"].notna() &
        d["order_date"].notna() &
        d["customer_phone"].notna() &
        d["quantity"].notna() &
        (d["quantity"] > 0) &
        (d["quantity"] <= 50) &
        (d["quantity"] % 1 == 0) &
        d["unit_price"].notna() &
        (d["unit_price"] > 0) &
        (d["unit_price"] == d["list_price"])
    )
    d = d[valid_mask].copy()
    d["quantity"] = d["quantity"].astype(int)

    cols = ["order_id", "order_date", "branch", "sku", "product_name", "category", "quantity", "unit_price", "customer_phone"]
    return d[cols].sort_values("order_date").reset_index(drop=True)

ctg_clean = clean_branch_orders(ctg_raw, "Chattogram", "order_id", "order_date", "%Y-%m-%d", "sku", True, "quantity", "price_bdt", "phone")
dhk_clean = clean_branch_orders(dhk_raw, "Dhaka", "Order ID", "Date", "%d/%m/%Y", "Product", False, "Qty", "Unit Price", "Customer Phone")
syl_clean = clean_branch_orders(syl_raw, "Sylhet", "ORDER_NO", "SALE_DATE", "%d-%b-%y", "ITEM_CODE", True, "QTY", "RATE", "MOBILE")

# 5. Merge All Orders + Time-Aware Unit Cost (Effective Date Lookup)
all_orders = pd.concat([dhk_clean, ctg_clean, syl_clean], ignore_index=True).sort_values("order_date").reset_index(drop=True)
all_orders = pd.merge_asof(
    all_orders,
    prod.sort_values("cost_valid_from")[["sku", "cost_valid_from", "unit_cost"]],
    left_on="order_date",
    right_on="cost_valid_from",
    by="sku",
    direction="backward"
).drop(columns=["cost_valid_from"])

all_orders["revenue"] = all_orders["quantity"] * all_orders["unit_price"]
all_orders["cogs"] = all_orders["quantity"] * all_orders["unit_cost"]
all_orders["gross_profit"] = all_orders["revenue"] - all_orders["cogs"]

# 6. Clean Returns & Join to Master Orders
ret = ret_raw.copy()
ret["order_id"] = ret["returned_order"].apply(clean_order_id)
ret["refund_date"] = pd.to_datetime(ret["refund_date"], format="%Y-%m-%d")
ret["reason"] = ret["reason"].astype(str).str.strip()
ret["is_valid_order"] = ret["order_id"].isin(all_orders["order_id"])

master = all_orders.merge(
    ret[ret["is_valid_order"]][["order_id", "refund_date", "reason"]],
    on="order_id",
    how="left"
)
master["is_returned"] = master["refund_date"].notna()
master["date_anomaly_flag"] = master["refund_date"] < master["order_date"]
master["net_revenue"] = np.where(master["is_returned"], 0.0, master["revenue"])
master["net_gross_profit"] = np.where(master["is_returned"], 0.0, master["gross_profit"])

# 7. Save All Cleaned Files
dhk_clean.to_csv(os.path.join(OUTPUT_DIR, "orders_dhaka_cleaned.csv"), index=False)
ctg_clean.to_csv(os.path.join(OUTPUT_DIR, "orders_chattogram_cleaned.csv"), index=False)
syl_clean.to_csv(os.path.join(OUTPUT_DIR, "orders_sylhet_cleaned.csv"), index=False)
prod.to_csv(os.path.join(OUTPUT_DIR, "products_cleaned.csv"), index=False)
ret[["order_id", "refund_date", "reason", "is_valid_order"]].to_csv(os.path.join(OUTPUT_DIR, "returns_cleaned.csv"), index=False)
master.to_csv(os.path.join(OUTPUT_DIR, "all_orders_with_returns_cleaned.csv"), index=False)

# 8. Print Financial & Margin Calculations
gross_rev = master["revenue"].sum()
refunded_amt = master.loc[master["is_returned"], "revenue"].sum()
net_rev = master["net_revenue"].sum()
net_cogs = master.loc[~master["is_returned"], "cogs"].sum()
net_profit = master["net_gross_profit"].sum()
margin_pct = (net_profit / net_rev) * 100

print("=" * 55)
print("OVERALL PERFORMANCE SUMMARY (AFTER RETURNS)")
print("=" * 55)
print(f"Valid Orders        : {len(master)} (Returned: {master['is_returned'].sum()})")
print(f"Gross Revenue       : {gross_rev:,.2f} BDT")
print(f"Refunded Amount     : {refunded_amt:,.2f} BDT")
print(f"Net Revenue         : {net_rev:,.2f} BDT")
print(f"Net COGS            : {net_cogs:,.2f} BDT")
print(f"Net Gross Profit    : {net_profit:,.2f} BDT")
print(f"Net Gross Margin %  : {margin_pct:.2f}%")

print("\n" + "=" * 55)
print("BREAKDOWN BY BRANCH")
print("=" * 55)
branch_summary = master.groupby("branch").agg(
    valid_orders=("order_id", "count"),
    returned_orders=("is_returned", "sum"),
    gross_revenue=("revenue", "sum"),
    refunded_amount=("revenue", lambda s: s[master.loc[s.index, "is_returned"]].sum()),
    net_revenue=("net_revenue", "sum"),
    net_profit=("net_gross_profit", "sum"),
)
branch_summary["margin_%"] = (branch_summary["net_profit"] / branch_summary["net_revenue"] * 100).round(2)
print(branch_summary.to_string())

print("\n" + "=" * 55)
print("BREAKDOWN BY PRODUCT CATEGORY")
print("=" * 55)
cat_summary = master.groupby("category").agg(
    units_sold=("quantity", lambda s: s[~master.loc[s.index, "is_returned"]].sum()),
    net_revenue=("net_revenue", "sum"),
    net_profit=("net_gross_profit", "sum"),
)
cat_summary["margin_%"] = (cat_summary["net_profit"] / cat_summary["net_revenue"] * 100).round(2)
print(cat_summary.to_string())
