import os
import glob
import re
import pandas as pd
import numpy as np

# 1. Ask user for folder path
folder_input = input("Enter the folder path containing your CSV files: ").strip().strip('"').strip("'")
if not os.path.isdir(folder_input):
    raise FileNotFoundError(f"Folder not found: {folder_input}")

output_dir = os.path.join(folder_input, "cleaned_output")
os.makedirs(output_dir, exist_ok=True)

# 2. Discover all CSV files in the folder
all_csvs = sorted(glob.glob(os.path.join(folder_input, "*.csv")))
if not all_csvs:
    raise FileNotFoundError(f"No CSV files found in: {folder_input}")

prod_files = [f for f in all_csvs if "product" in os.path.basename(f).lower()]
ret_files  = [f for f in all_csvs if "return" in os.path.basename(f).lower()]
order_files = [f for f in all_csvs if f not in prod_files and f not in ret_files]

if not prod_files:
    raise FileNotFoundError("Missing products catalog CSV (e.g., products.csv) in the folder.")
if not order_files:
    raise FileNotFoundError("No district order CSV files found in the folder.")

print(f"\nFound {len(order_files)} district order file(s):")
for f in order_files:
    print(f"  - {os.path.basename(f)}")

# 3. Parsing & Schema-Matching Helpers
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

def parse_flexible_date(series):
    s = series.astype(str).str.strip()
    res = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d-%b-%y", "%d-%b-%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"]:
        mask = res.isna()
        if not mask.any():
            break
        res.loc[mask] = pd.to_datetime(s.loc[mask], format=fmt, errors="coerce")
    mask = res.isna()
    if mask.any():
        res.loc[mask] = pd.to_datetime(s.loc[mask], errors="coerce", dayfirst=True)
    return res

def find_col(cols, patterns):
    norm = {c: re.sub(r"[^a-z0-9]", "", c.lower()) for c in cols}
    for pat in patterns:
        for orig, n in norm.items():
            if re.search(pat, n):
                return orig
    return None

# 4. Clean Products Catalog
prod = pd.read_csv(prod_files[0])
for col in ["sku", "product_name", "category"]:
    prod[col] = prod[col].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
prod["sku"] = prod["sku"].str.upper()
prod["list_price"] = pd.to_numeric(prod["list_price"])
prod["unit_cost"] = pd.to_numeric(prod["unit_cost"])
prod["cost_valid_from"] = parse_flexible_date(prod["cost_valid_from"])
prod = prod.drop_duplicates().sort_values(["sku", "cost_valid_from"]).reset_index(drop=True)

prod_lookup = prod[["sku", "product_name", "category", "list_price"]].drop_duplicates().copy()
prod_lookup["name_key"] = prod_lookup["product_name"].str.lower()

# 5. Clean Every District Order File Automatically
cleaned_district_dfs = []

for filepath in order_files:
    fname = os.path.basename(filepath)
    df = pd.read_csv(filepath)

    id_col    = find_col(df.columns, [r"orderid", r"orderno", r"^id$"])
    date_col  = find_col(df.columns, [r"date"])
    sku_col   = find_col(df.columns, [r"sku", r"itemcode", r"productcode"])
    prod_col  = find_col(df.columns, [r"product", r"itemname", r"item"])
    qty_col   = find_col(df.columns, [r"qty", r"quantity"])
    price_col = find_col(df.columns, [r"price", r"rate", r"amount"])
    phone_col = find_col(df.columns, [r"phone", r"mobile", r"contact"])

    if not all([id_col, date_col, (sku_col or prod_col), qty_col, price_col, phone_col]):
        print(f"  [WARNING] Skipping {fname}: could not auto-detect required columns ({list(df.columns)})")
        continue

    # Derive clean district/branch name from filename (e.g., orders_dhaka.csv -> Dhaka)
    district_name = re.sub(r"^orders?[_\-\s]*", "", os.path.splitext(fname)[0], flags=re.I).strip().title()
    if district_name.lower() in ["ctg", "chittagong"]:
        district_name = "Chattogram"

    d = pd.DataFrame()
    d["order_id"]       = df[id_col].apply(clean_order_id)
    d["order_date"]     = parse_flexible_date(df[date_col])
    d["branch"]         = district_name
    d["quantity"]       = df[qty_col].apply(clean_qty)
    d["unit_price"]     = df[price_col].apply(clean_price)
    d["customer_phone"] = df[phone_col].apply(clean_phone)

    if sku_col:
        d["sku"] = df[sku_col].apply(clean_sku)
        d = d.merge(prod_lookup[["sku", "product_name", "category", "list_price"]], on="sku", how="left")
    else:
        d["name_key"] = df[prod_col].astype(str).str.strip().str.replace(r"\s+", " ", regex=True).str.lower()
        d = d.merge(prod_lookup[["sku", "product_name", "category", "list_price", "name_key"]], on="name_key", how="left")

    # Remove exact and disguised duplicates
    d = d.drop_duplicates(subset=["order_id", "order_date", "branch", "sku", "quantity", "unit_price", "customer_phone"])

    # Strict validation: remove null/zero/negative/outlier qty & price, unmapped SKUs, bad dates/phones
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
    d = d[cols].sort_values("order_date").reset_index(drop=True)

    # Save individual cleaned district file
    out_name = f"{os.path.splitext(fname)[0]}_cleaned.csv"
    d.to_csv(os.path.join(output_dir, out_name), index=False)
    cleaned_district_dfs.append(d)

# 6. Combine All Districts & Attach Time-Aware Unit Cost
all_orders = pd.concat(cleaned_district_dfs, ignore_index=True).sort_values("order_date").reset_index(drop=True)
all_orders = pd.merge_asof(
    all_orders,
    prod.sort_values("cost_valid_from")[["sku", "cost_valid_from", "unit_cost"]],
    left_on="order_date",
    right_on="cost_valid_from",
    by="sku",
    direction="backward"
).drop(columns=["cost_valid_from"])

all_orders["revenue"]      = all_orders["quantity"] * all_orders["unit_price"]
all_orders["cogs"]         = all_orders["quantity"] * all_orders["unit_cost"]
all_orders["gross_profit"] = all_orders["revenue"] - all_orders["cogs"]

# 7. Reconcile Returns (if returns.csv exists in folder)
if ret_files:
    ret = pd.read_csv(ret_files[0])
    ret_id_col   = find_col(ret.columns, [r"return", r"order", r"id"])
    ret_date_col = find_col(ret.columns, [r"date"])
    reason_col   = find_col(ret.columns, [r"reason", r"note", r"comment"])

    ret["order_id"]       = ret[ret_id_col].apply(clean_order_id)
    ret["refund_date"]    = parse_flexible_date(ret[ret_date_col])
    ret["reason"]         = ret[reason_col].astype(str).str.strip() if reason_col else "Unknown"
    ret["is_valid_order"] = ret["order_id"].isin(all_orders["order_id"])
    ret = ret.drop_duplicates(subset=["order_id"])

    ret[["order_id", "refund_date", "reason", "is_valid_order"]].to_csv(
        os.path.join(output_dir, "returns_cleaned.csv"), index=False
    )

    master = all_orders.merge(
        ret[ret["is_valid_order"]][["order_id", "refund_date", "reason"]],
        on="order_id",
        how="left"
    )
else:
    master = all_orders.copy()
    master["refund_date"] = pd.NaT
    master["reason"] = np.nan

master["is_returned"]       = master["refund_date"].notna()
master["date_anomaly_flag"] = master["refund_date"] < master["order_date"]
master["net_revenue"]       = np.where(master["is_returned"], 0.0, master["revenue"])
master["net_gross_profit"]  = np.where(master["is_returned"], 0.0, master["gross_profit"])

prod.to_csv(os.path.join(output_dir, "products_cleaned.csv"), index=False)
master.to_csv(os.path.join(output_dir, "all_districts_master_cleaned.csv"), index=False)

# 8. Print Financial & Margin Report
gross_rev    = master["revenue"].sum()
refunded_amt = master.loc[master["is_returned"], "revenue"].sum()
net_rev      = master["net_revenue"].sum()
net_cogs     = master.loc[~master["is_returned"], "cogs"].sum()
net_profit   = master["net_gross_profit"].sum()
margin_pct   = (net_profit / net_rev) * 100 if net_rev > 0 else 0.0

print("\n" + "=" * 60)
print("OVERALL MULTI-DISTRICT PERFORMANCE (AFTER RETURNS)")
print("=" * 60)
print(f"Districts Processed : {master['branch'].nunique()}")
print(f"Valid Orders        : {len(master)} (Returned: {master['is_returned'].sum()})")
print(f"Gross Revenue       : {gross_rev:,.2f} BDT")
print(f"Refunded Amount     : {refunded_amt:,.2f} BDT")
print(f"Net Revenue         : {net_rev:,.2f} BDT")
print(f"Net COGS            : {net_cogs:,.2f} BDT")
print(f"Net Gross Profit    : {net_profit:,.2f} BDT")
print(f"Net Gross Margin %  : {margin_pct:.2f}%")

print("\n" + "=" * 60)
print("BREAKDOWN BY DISTRICT / BRANCH")
print("=" * 60)
branch_summary = master.groupby("branch").agg(
    valid_orders=("order_id", "count"),
    returned_orders=("is_returned", "sum"),
    gross_revenue=("revenue", "sum"),
    refunded_amount=("revenue", lambda s: s[master.loc[s.index, "is_returned"]].sum()),
    net_revenue=("net_revenue", "sum"),
    net_profit=("net_gross_profit", "sum"),
)
branch_summary["margin_%"] = (branch_summary["net_profit"] / branch_summary["net_revenue"] * 100).round(2)
print(branch_summary.sort_values("net_revenue", ascending=False).to_string())

print("\n" + "=" * 60)
print("BREAKDOWN BY PRODUCT CATEGORY")
print("=" * 60)
cat_summary = master.groupby("category").agg(
    units_sold=("quantity", lambda s: s[~master.loc[s.index, "is_returned"]].sum()),
    net_revenue=("net_revenue", "sum"),
    net_profit=("net_gross_profit", "sum"),
)
cat_summary["margin_%"] = (cat_summary["net_profit"] / cat_summary["net_revenue"] * 100).round(2)
print(cat_summary.sort_values("net_revenue", ascending=False).to_string())
print(f"\nAll cleaned files saved to: {output_dir}")