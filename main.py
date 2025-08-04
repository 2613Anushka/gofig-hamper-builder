import streamlit as st
import pandas as pd
from datetime import datetime
import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# ===== CONFIGURATION =====
CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQoh6-PUp4dlT0syi9F5SFCVFjS7eVT-Ra_K0_N1b-2sYr2yDRRP6-w-ZoVidc3PSuK9TfG_eGzLOH4/pub?gid=436661680&single=true&output=csv"
st.set_page_config(layout="wide")
st.title("🏱 Gofig Hamper Builder")

# ===== LOAD DATA =====
@st.cache_data(ttl=300)
def load_data():
    df_raw = pd.read_csv(CSV_URL)
    df_raw["MRP"] = pd.to_numeric(df_raw["MRP"], errors="coerce")
    df_raw["Available Units"] = pd.to_numeric(df_raw["Available Units"], errors="coerce")
    df_raw["Discount Percentage"] = pd.to_numeric(df_raw.get("Discount Percentage", 0), errors="coerce").fillna(0)
    df_raw["Expiry Date"] = pd.to_datetime(df_raw["Expiry Date"], format="%d-%b-%Y", errors="coerce")
    df_raw["Shipping Weight (grams)"] = pd.to_numeric(df_raw.get("Shipping Weight (grams)"), errors="coerce").fillna(0)
    df_raw["Brand Name"] = df_raw["Brand Name"].fillna("Unknown")
    df_raw["SKU"] = df_raw["SKU"].fillna("")
    df_raw["Category"] = df_raw["Category"].fillna("Misc")
    df_filtered = df_raw.dropna(subset=["Item Name", "MRP", "Available Units", "Expiry Date", "Inventory Holding", "Product Status"])
    return df_raw, df_filtered

data_raw, data = load_data()

item_names = sorted(data_raw["Item Name"].dropna().unique())
brand_options = sorted(data_raw["Brand Name"].dropna().unique())
available_categories = sorted(data["Category"].unique())
inventory_options = sorted(data["Inventory Holding"].dropna().unique())
status_options = sorted(data["Product Status"].dropna().unique())

for key in ['saved_hampers', 'box_applied', 'hamper', 'replacements', 'additional_items']:
    if key not in st.session_state:
        st.session_state[key] = [] if 'hamper' in key or 'items' in key else None

def is_within_expiry(expiry_date, min_days, max_days):
    today = datetime.today().date()
    days_until_expiry = (expiry_date.date() - today).days
    return min_days <= days_until_expiry <= max_days

def apply_box_type_defaults(box_type):
    if st.session_state.box_applied == box_type:
        return
    if box_type == "Steal Deal":
        st.session_state.min_days = 15
        st.session_state.max_days = 60
    elif box_type == "Green Box":
        st.session_state.min_days = 40
        st.session_state.max_days = 120
    elif box_type == "Gift Box":
        st.session_state.min_days = 45
        st.session_state.max_days = 730
    st.session_state.box_applied = box_type

def create_hamper(budget, selected_categories, selected_inventory, selected_status, selected_brands, min_days, max_days, box_type):
    hamper = []
    total_cost = 0
    target_budget = budget * 0.99  # Target 99% of budget
    
    df_filtered = data[
        (data["Category"].isin(selected_categories)) &
        (data["Inventory Holding"].isin(selected_inventory)) &
        (data["Product Status"].isin(selected_status)) &
        (data["Brand Name"].isin(selected_brands)) &
        (data["Expiry Date"].apply(lambda x: is_within_expiry(x, min_days, max_days)))
    ]
    
    if df_filtered.empty:
        return hamper, total_cost

    # Create comprehensive item pool with strategic quantity ranges
    items_pool = []
    unique_items = df_filtered.drop_duplicates(subset=['Item Name', 'Category'])
    
    for _, row in unique_items.iterrows():
        max_available = min(int(row["Available Units"]), 20)  # Increased max quantity
        mrp = float(row["MRP"])
        
        # Create entries for different quantity ranges
        for qty in range(1, max_available + 1):
            items_pool.append({
                'category': row["Category"],
                'name': row["Item Name"],
                'mrp': mrp,
                'qty': qty,
                'total_cost': mrp * qty,
                'available_units': max_available,
                'cost_per_unit': mrp
            })
    
    # Sort items by total cost for different strategies
    items_by_cost = sorted(items_pool, key=lambda x: x['total_cost'])
    items_by_efficiency = sorted(items_pool, key=lambda x: x['cost_per_unit'])
    
    # Phase 1: Multi-strategy approach to fill budget
    used_items = {}
    strategies = [
        ('cost_ascending', items_by_cost),
        ('efficiency', items_by_efficiency),
        ('cost_descending', sorted(items_pool, key=lambda x: x['total_cost'], reverse=True))
    ]
    
    best_hamper = []
    best_total = 0
    
    for strategy_name, sorted_items in strategies:
        temp_hamper = []
        temp_total = 0
        temp_used = {}
        
        for item in sorted_items:
            item_key = (item['category'], item['name'])
            
            # Skip if we already have this item with higher or equal quantity
            if item_key in temp_used and temp_used[item_key] >= item['qty']:
                continue
            
            # Calculate potential cost
            potential_cost = temp_total + item['total_cost']
            
            # For cost_descending strategy, be more permissive with budget
            budget_limit = budget * 1.02 if strategy_name == 'cost_descending' else budget
            
            if potential_cost <= budget_limit:
                # Remove previous entry of this item if exists
                if item_key in temp_used:
                    for i, (cat, name, old_qty) in enumerate(temp_hamper):
                        if cat == item['category'] and name == item['name']:
                            old_cost = item['mrp'] * old_qty
                            temp_total -= old_cost
                            temp_hamper.pop(i)
                            break
                
                # Add the item with new quantity
                temp_hamper.append((item['category'], item['name'], item['qty']))
                temp_total += item['total_cost']
                temp_used[item_key] = item['qty']
        
        # Keep the best result so far
        if temp_total > best_total and temp_total <= budget:
            best_hamper = temp_hamper.copy()
            best_total = temp_total
            used_items = temp_used.copy()
    
    hamper = best_hamper
    total_cost = best_total
    
    # Phase 2: Aggressive optimization for higher budget utilization
    max_iterations = 100
    iteration = 0
    
    while total_cost < target_budget and iteration < max_iterations:
        iteration += 1
        improved = False
        remaining_budget = budget - total_cost
        
        # Strategy 1: Try to increase quantities of existing items
        for cat, name, current_qty in hamper[:]:
            item_key = (cat, name)
            item_match = df_filtered[(df_filtered["Category"] == cat) & 
                                   (df_filtered["Item Name"] == name)]
            
            if not item_match.empty:
                mrp = float(item_match.iloc[0]["MRP"])
                max_available = min(int(item_match.iloc[0]["Available Units"]), 20)
                
                # Try to increase quantity
                for new_qty in range(current_qty + 1, max_available + 1):
                    cost_increase = mrp * (new_qty - current_qty)
                    if cost_increase <= remaining_budget:
                        # Update the hamper
                        for i, (h_cat, h_name, h_qty) in enumerate(hamper):
                            if h_cat == cat and h_name == name:
                                hamper[i] = (cat, name, new_qty)
                                total_cost += cost_increase
                                used_items[item_key] = new_qty
                                improved = True
                                break
                        if improved:
                            break
            if improved:
                break
        
        # Strategy 2: Add new items if no improvement from quantity increase
        if not improved:
            # Find items not yet in hamper
            available_items = []
            for item in items_pool:
                item_key = (item['category'], item['name'])
                if item_key not in used_items and item['total_cost'] <= remaining_budget:
                    available_items.append(item)
            
            # Sort by cost descending to try larger items first
            available_items.sort(key=lambda x: x['total_cost'], reverse=True)
            
            for item in available_items:
                if item['total_cost'] <= remaining_budget:
                    item_key = (item['category'], item['name'])
                    hamper.append((item['category'], item['name'], item['qty']))
                    total_cost += item['total_cost']
                    used_items[item_key] = item['qty']
                    improved = True
                    break
        
        if not improved:
            break
    
    # Phase 3: Final optimization - try to squeeze in any remaining budget
    final_budget = budget - total_cost
    if final_budget > 0:
        # Look for small items or single units to fill remaining budget
        small_items = []
        for item in items_pool:
            item_key = (item['category'], item['name'])
            if item['total_cost'] <= final_budget:
                if item_key not in used_items:
                    small_items.append(item)
                elif used_items[item_key] < item['qty']:
                    # Check if we can increase quantity
                    cost_increase = item['mrp'] * (item['qty'] - used_items[item_key])
                    if cost_increase <= final_budget:
                        small_items.append(item)
        
        # Sort by cost descending to get maximum value
        small_items.sort(key=lambda x: x['total_cost'], reverse=True)
        
        for item in small_items:
            item_key = (item['category'], item['name'])
            if item_key not in used_items:
                if total_cost + item['total_cost'] <= budget:
                    hamper.append((item['category'], item['name'], item['qty']))
                    total_cost += item['total_cost']
                    used_items[item_key] = item['qty']
            else:
                # Try to increase quantity
                current_qty = used_items[item_key]
                if current_qty < item['qty']:
                    cost_increase = item['mrp'] * (item['qty'] - current_qty)
                    if total_cost + cost_increase <= budget:
                        # Update hamper
                        for i, (h_cat, h_name, h_qty) in enumerate(hamper):
                            if h_cat == item['category'] and h_name == item['name']:
                                hamper[i] = (item['category'], item['name'], item['qty'])
                                total_cost += cost_increase
                                used_items[item_key] = item['qty']
                                break
    
    return hamper, total_cost

def get_replacement_suggestions(category, current_item, selected_inventory, selected_status, selected_brands, min_days, max_days):
    """Get replacement suggestions for an item"""
    df_filtered = data[
        (data["Category"] == category) &
        (data["Item Name"] != current_item) &
        (data["Inventory Holding"].isin(selected_inventory)) &
        (data["Product Status"].isin(selected_status)) &
        (data["Brand Name"].isin(selected_brands)) &
        (data["Expiry Date"].apply(lambda x: is_within_expiry(x, min_days, max_days)))
    ]
    
    # Sort by MRP to show variety
    df_filtered = df_filtered.sort_values('MRP').head(7)
    
    suggestions = []
    for _, row in df_filtered.iterrows():
        suggestions.append({
            'name': row["Item Name"],
            'mrp': float(row["MRP"]),
            'available': int(row["Available Units"]),
            'expiry': row["Expiry Date"].strftime("%d-%b-%Y"),
            'brand': row["Brand Name"]
        })
    
    return suggestions

# ===== SIDEBAR UI =====
with st.sidebar:
    st.header("Customize Your Hamper")
    budget = st.number_input("Set Budget (₹)", min_value=100, max_value=100000, value=1000)
    box_type = st.radio("Select Box Type", ["Gift Box", "Green Box", "Steal Deal"], key="box_type")
    apply_box_type_defaults(box_type)
    min_days = st.slider("Min Days to Expiry", 0, 365, st.session_state.get("min_days", 30), key="min_days")
    max_days = st.slider("Max Days to Expiry", min_days+1, 730, st.session_state.get("max_days", 365), key="max_days")
    selected_categories = st.multiselect("Choose Categories", available_categories, default=available_categories)
    selected_inventory = st.multiselect("Inventory Holding", inventory_options, default=inventory_options)
    selected_status = st.multiselect("Product Status", status_options, default=status_options)
    selected_brands = st.multiselect("Brand Name", brand_options, default=brand_options)

    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.experimental_rerun()

    if st.button("🎉 Create Hamper"):
        hamper, total = create_hamper(budget, selected_categories, selected_inventory,
                                      selected_status, selected_brands, min_days, max_days, box_type)
        st.session_state.hamper = hamper
        st.session_state.total = total
        st.session_state.replacements = {}

        budget_utilization = (total / budget) * 100
        if budget_utilization >= 99 and budget_utilization <= 100:
            st.success(f"Perfect! Hamper created with {budget_utilization:.1f}% budget utilization (₹{total:.2f})")
        elif total > budget:
            st.warning(f"Hamper exceeds budget by ₹{total - budget:.2f}")
        else:
            st.info(f"Hamper created with {budget_utilization:.1f}% budget utilization (₹{total:.2f})")

# ===== MAIN DISPLAY SECTION =====
if st.session_state.hamper or st.session_state.additional_items:
    st.subheader("🧺 Your Hamper")

    # Merge hamper and additional items
    all_hamper_items = st.session_state.hamper.copy()
    if st.session_state.additional_items:
        all_hamper_items.extend(st.session_state.additional_items)
    
    # Update session state with merged items
    st.session_state.hamper = all_hamper_items
    st.session_state.additional_items = []  # Clear additional items after merging

    to_remove = []
    for idx, (cat, name, qty) in enumerate(all_hamper_items):
        # Try to find item in filtered data first, then raw data
        item_df = data[data["Item Name"] == name]
        if not item_df.empty and cat != "Misc":
            item_df = item_df[item_df["Category"] == cat]
        
        # Fallback to raw data if not found in filtered data
        if item_df.empty:
            item_df = data_raw[data_raw["Item Name"] == name]
        
        if item_df.empty:
            st.error(f"Item '{name}' not found in database")
            continue
            
        item = item_df.iloc[0]
        price = float(item["MRP"]) if pd.notnull(item["MRP"]) else 0
        available = int(item["Available Units"]) if pd.notnull(item["Available Units"]) else 1
        expiry = item["Expiry Date"].strftime("%d-%b-%Y") if pd.notnull(item["Expiry Date"]) else "Missing"
        safe_qty = min(int(qty), available)

        col1, col2, col3, col4, col5 = st.columns([3, 1, 1, 1, 0.5])
        with col1:
            st.markdown(f"**{name}**  \n*Category:* {cat}  \n*Expiry:* {expiry}  \n*Available:* {available}")
        with col2:
            qty_input = st.number_input("Qty", min_value=1, max_value=max(available, 1), value=safe_qty, key=f"qty_{idx}")
            all_hamper_items[idx] = (cat, name, qty_input)
        with col3:
            st.write(f"₹{qty_input * price:.2f}")
        with col4:
            if st.button("Replace", key=f"replace_{idx}"):
                st.session_state.replacements[idx] = (cat, name)
        with col5:
            if st.button("🗑", key=f"delete_{idx}"):
                to_remove.append(idx)

        # Show replacement suggestions if this item is selected for replacement
        if idx in st.session_state.replacements:
            st.write("**Replacement Suggestions:**")
            suggestions = get_replacement_suggestions(
                cat, name, selected_inventory, selected_status, selected_brands, min_days, max_days
            )
            
            if suggestions:
                cols = st.columns(min(len(suggestions), 3))
                for i, suggestion in enumerate(suggestions):
                    with cols[i % 3]:
                        st.write(f"**{suggestion['name']}**")
                        st.write(f"₹{suggestion['mrp']:.2f}")
                        st.write(f"Available: {suggestion['available']}")
                        st.write(f"Expiry: {suggestion['expiry']}")
                        
                        if st.button(f"Replace with this", key=f"replace_with_{idx}_{i}"):
                            # Replace the item
                            all_hamper_items[idx] = (cat, suggestion['name'], 1)
                            # Remove from replacements
                            del st.session_state.replacements[idx]
                            st.success(f"Replaced {name} with {suggestion['name']}")
                            st.rerun()
                
                if st.button("Cancel Replacement", key=f"cancel_replace_{idx}"):
                    del st.session_state.replacements[idx]
                    st.rerun()
            else:
                st.write("No replacement suggestions available for this category.")
                if st.button("Cancel Replacement", key=f"cancel_replace_{idx}"):
                    del st.session_state.replacements[idx]
                    st.rerun()

    # Remove deleted items
    for idx in sorted(to_remove, reverse=True):
        del all_hamper_items[idx]
        # Also remove from replacements if it exists
        if idx in st.session_state.replacements:
            del st.session_state.replacements[idx]
    
    # Update session state
    st.session_state.hamper = all_hamper_items
    
    if to_remove:
        st.rerun()

    # ==== DISPLAY TOTAL AND BUDGET CHECK ====
    total_cost = 0
    for cat, name, qty in all_hamper_items:
        # Try to find in filtered data first, then raw data
        item_match = data[data["Item Name"] == name]
        if not item_match.empty and cat != "Misc":
            item_match = item_match[item_match["Category"] == cat]
        if item_match.empty:
            item_match = data_raw[data_raw["Item Name"] == name]
        
        if not item_match.empty:
            price = float(item_match.iloc[0]["MRP"]) if pd.notnull(item_match.iloc[0]["MRP"]) else 0
            total_cost += int(qty) * price
    
    st.markdown("---")
    col1, col2 = st.columns([1, 2])
    with col1:
        st.metric("Total Hamper Cost (₹)", f"{total_cost:.2f}")
    with col2:
        budget_utilization = (total_cost / budget) * 100
        if total_cost > budget:
            st.warning(f"⚠️ Hamper exceeds budget by ₹{total_cost - budget:.2f}")
        elif budget_utilization >= 99:
            st.success(f"✅ Perfect budget utilization: {budget_utilization:.1f}%")
        else:
            st.info(f"Budget utilization: {budget_utilization:.1f}% (₹{budget - total_cost:.2f} remaining)")

    # ========== ADD ITEM ==========
    st.subheader("➕ Add More Items")
    add_item = st.selectbox("Search by Item Name", ["-- Select Item --"] + item_names)
    if add_item and add_item != "-- Select Item --":
        # Try to find the item in the full dataset first
        match = data_raw[data_raw["Item Name"] == add_item]
        if not match.empty:
            item = match.iloc[0]
            # Handle missing values with "Missing" placeholder
            max_qty = int(item.get("Available Units", 1)) if pd.notnull(item.get("Available Units")) else 1
            expiry = item["Expiry Date"].strftime("%d-%b-%Y") if pd.notnull(item.get("Expiry Date")) else "Missing"
            mrp = item.get("MRP", 0) if pd.notnull(item.get("MRP")) else 0
            category = item.get("Category", "Misc") if pd.notnull(item.get("Category")) else "Misc"
            
            st.write(f"*MRP:* ₹{mrp} | *Expiry:* {expiry} | *Available Units:* {max_qty} | *Category:* {category}")
            
            col1, col2 = st.columns([1, 1])
            with col1:
                qty = st.number_input("Quantity", 1, min(max_qty, 10), 1, key="add_qty")
            with col2:
                if st.button("Add Item", key="add_item_btn"):
                    # Initialize additional_items if not exists
                    if 'additional_items' not in st.session_state:
                        st.session_state.additional_items = []
                    
                    # Add to additional items
                    st.session_state.additional_items.append((category, add_item, qty))
                    st.success(f"Added {qty} x {add_item} to hamper!")
                    st.rerun()

    # ========== EXPORT ==========
    final_items = all_hamper_items
    df_export = []
    for cat, name, qty in final_items:
        # Try to find item in filtered data first, then in raw data
        row = data[data["Item Name"] == name]
        if not row.empty and cat != "Misc":
            row = row[row["Category"] == cat]
        if row.empty:
            # Fallback to raw data
            row = data_raw[data_raw["Item Name"] == name]
        
        if row.empty:
            continue
        
        r = row.iloc[0]
        total_amt = (r["MRP"] if pd.notnull(r["MRP"]) else 0) * qty
        weight = r["Shipping Weight (grams)"] if pd.notnull(r["Shipping Weight (grams)"]) else 0
        
        df_export.append({
            "Item Name": name,
            "SKU": r["SKU"] if pd.notnull(r["SKU"]) else "Missing",
            "Expiry Date": r["Expiry Date"].strftime("%d-%b-%Y") if pd.notnull(r["Expiry Date"]) else "Missing",
            "Quantity": qty,
            "MRP": r["MRP"] if pd.notnull(r["MRP"]) else "Missing",
            "Available Qty": int(r["Available Units"]) if pd.notnull(r["Available Units"]) else "Missing",
            "Shipping Weight (grams)": weight,
            "Total Amt": total_amt,
            "Total Weight (g)": qty * weight,
            "CO2e/Unit": "Missing",
            "Total CO2e": "Missing"
        })

    df_final = pd.DataFrame(df_export)
    # Add TOTAL row
    subtotal_row = pd.DataFrame([{
        "Item Name": "TOTAL", "SKU": "", "Expiry Date": "", "Quantity": "",
        "MRP": "", "Available Qty": "", "Shipping Weight (grams)": "",
        "Total Amt": df_final["Total Amt"].sum(),
        "Total Weight (g)": df_final["Total Weight (g)"].sum(),
        "CO2e/Unit": "", "Total CO2e": ""
    }])
    df_final = pd.concat([df_final, subtotal_row], ignore_index=True)

    st.download_button("⬇ Download CSV", df_final.to_csv(index=False), "hamper.csv")

    def generate_pdf(df):
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        x, y = 40, height - 40
        c.setFont("Helvetica-Bold", 14)
        c.drawString(x, y, "Gofig Hamper Summary")
        y -= 30
        headers = list(df.columns)
        col_widths = [80] * len(headers)
        c.setFont("Helvetica-Bold", 9)
        for i, h in enumerate(headers):
            c.drawString(x + sum(col_widths[:i]), y, h)
        y -= 20
        c.setFont("Helvetica", 8)
        for _, row in df.iterrows():
            for i, h in enumerate(headers):
                c.drawString(x + sum(col_widths[:i]), y, str(row[h]))
            y -= 15
            if y < 50:
                c.showPage()
                y = height - 40
                c.setFont("Helvetica-Bold", 9)
                for i, h in enumerate(headers):
                    c.drawString(x + sum(col_widths[:i]), y, h)
                y -= 20
                c.setFont("Helvetica", 8)
        c.save()
        buffer.seek(0)
        return buffer

    st.download_button("⬇ Download PDF", generate_pdf(df_final), file_name="hamper.pdf", mime="application/pdf")
