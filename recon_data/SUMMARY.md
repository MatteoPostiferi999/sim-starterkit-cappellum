# RestBench Recon Summary


## Suppliers (5)

- **Canal Dairy Co.** — lead 1d, delivers ['Tuesday', 'Thursday', 'Saturday'], min 5.0kg
  - ingredients: Mozzarella (€9.5/kg), Cream (€3.8/kg)
  - seen in: baseline_seed42, baseline_seed88, baseline_seed123, supply_crisis_seed42, supply_crisis_seed88, supply_crisis_seed123, tourist_season_seed42, tourist_season_seed88, tourist_season_seed123, renovation_seed42, renovation_seed88, renovation_seed123
- **Fresh Farms NL** — lead 1d, delivers ['Monday', 'Wednesday', 'Friday'], min 5.0kg
  - ingredients: Tomato Sauce (€3.1/kg), Mushrooms (€4.2/kg), Lettuce (€2.8/kg), Chicken (€8.5/kg)
  - seen in: baseline_seed42, baseline_seed88, baseline_seed123, supply_crisis_seed42, supply_crisis_seed88, supply_crisis_seed123, tourist_season_seed42, tourist_season_seed88, tourist_season_seed123, renovation_seed42, renovation_seed88, renovation_seed123
- **Italian Imports Co.** — lead 3d, delivers ['Wednesday'], min 10.0kg
  - ingredients: Flour (€1.5/kg), Fresh Pasta (€4.8/kg), Pepperoni (€12.0/kg), Tomato Sauce (€2.6/kg)
  - seen in: baseline_seed42, baseline_seed88, baseline_seed123, supply_crisis_seed42, supply_crisis_seed88, supply_crisis_seed123, tourist_season_seed42, tourist_season_seed88, tourist_season_seed123, renovation_seed42, renovation_seed88, renovation_seed123
- **Nordic Fish Co.** — lead 1d, delivers ['Monday', 'Thursday'], min 5.0kg
  - ingredients: Salmon (€18.5/kg)
  - seen in: baseline_seed42, baseline_seed88, baseline_seed123, supply_crisis_seed42, supply_crisis_seed88, supply_crisis_seed123, tourist_season_seed42, tourist_season_seed88, tourist_season_seed123, renovation_seed42, renovation_seed88, renovation_seed123
- **North Sea Millers** — lead 2d, delivers ['Monday', 'Wednesday', 'Friday'], min 10.0kg
  - ingredients: Flour (€1.8/kg), Fresh Pasta (€5.5/kg)
  - seen in: baseline_seed42, baseline_seed88, baseline_seed123, supply_crisis_seed42, supply_crisis_seed88, supply_crisis_seed123, tourist_season_seed42, tourist_season_seed88, tourist_season_seed123, renovation_seed42, renovation_seed88, renovation_seed123

## Recipes (8)


### Mains
- **Chicken Parmesan** — base €20.0 — uses: Chicken 0.18kg, Tomato Sauce 0.08kg, Mozzarella 0.06kg
- **Grilled Salmon** — base €24.0 — uses: Salmon 0.2kg
- **Mushroom Risotto** — base €19.0 — uses: Mushrooms 0.12kg, Cream 0.1kg

### Pasta
- **Mushroom Tagliatelle** — base €17.5 — uses: Fresh Pasta 0.18kg, Cream 0.12kg, Mushrooms 0.09kg
- **Spaghetti Carbonara** — base €16.5 — uses: Fresh Pasta 0.18kg, Cream 0.08kg

### Pizza
- **Pizza Margherita** — base €14.5 — uses: Flour 0.25kg, Tomato Sauce 0.09kg, Mozzarella 0.11kg
- **Pizza Pepperoni** — base €16.0 — uses: Flour 0.25kg, Tomato Sauce 0.085kg, Mozzarella 0.1kg, Pepperoni 0.07kg

### Salad
- **Chicken Caesar Salad** — base €15.0 — uses: Chicken 0.14kg, Lettuce 0.12kg

## Alerts by scenario


### `baseline`
  (none observed in survival recon)

### `supply_crisis`
  - days [1]: Industry analysts warn of potential disruptions in Mediterranean shipping lanes.

### `tourist_season`
  (none observed in survival recon)

### `renovation`
  - days [1]: The dining room renovation begins today. Half your tables are unavailable for the next two weeks.

## Supplier reliability (under survival recon)

- **Nordic Fish Co.**: on-time 22%, short 78%, zero-fill 0%, avg fill 77% (45 unique deliveries)
- **Canal Dairy Co.**: on-time 18%, short 82%, zero-fill 0%, avg fill 79% (67 unique deliveries)
- **Italian Imports Co.**: on-time 18%, short 82%, zero-fill 0%, avg fill 76% (67 unique deliveries)
- **Fresh Farms NL**: on-time 8%, short 92%, zero-fill 0%, avg fill 73% (130 unique deliveries)

## Weather distribution (per scenario+seed)

- **baseline_seed42**: rainy=13/31, cloudy=8/31, stormy=5/31, sunny=5/31
- **baseline_seed88**: sunny=13/31, rainy=10/31, cloudy=8/31
- **baseline_seed123**: cloudy=15/31, rainy=11/31, sunny=5/31
- **supply_crisis_seed42**: rainy=13/31, cloudy=8/31, stormy=5/31, sunny=5/31
- **supply_crisis_seed88**: sunny=13/31, rainy=10/31, cloudy=8/31
- **supply_crisis_seed123**: cloudy=15/31, rainy=11/31, sunny=5/31
- **tourist_season_seed42**: rainy=13/31, cloudy=8/31, stormy=5/31, sunny=5/31
- **tourist_season_seed88**: sunny=13/31, rainy=10/31, cloudy=8/31
- **tourist_season_seed123**: cloudy=15/31, rainy=11/31, sunny=5/31
- **renovation_seed42**: rainy=13/31, cloudy=8/31, stormy=5/31, sunny=5/31
- **renovation_seed88**: sunny=13/31, rainy=10/31, cloudy=8/31
- **renovation_seed123**: cloudy=15/31, rainy=11/31, sunny=5/31

## Scenario finals (survival recon agent)


### `baseline`
  - seed=42: total=-15817, profit=-11432, sat_pen=0.0, rep_pen=2255.41, walk_pen=1909.0, waste_pen=221.34, days=30, status=completed, final_cash=3568.26
  - seed=88: total=-10486, profit=-6075, sat_pen=0.0, rep_pen=2350.63, walk_pen=1962.0, waste_pen=98.52, days=30, status=completed, final_cash=8925.47
  - seed=123: total=-10231, profit=-6857, sat_pen=0.0, rep_pen=1303.83, walk_pen=1937.0, waste_pen=132.73, days=30, status=completed, final_cash=8142.88

### `supply_crisis`
  - seed=42: total=-15252, profit=-11237, sat_pen=0.0, rep_pen=2012.41, walk_pen=1820.0, waste_pen=182.32, days=30, status=completed, final_cash=3762.61
  - seed=88: total=-13338, profit=-8589, sat_pen=0.0, rep_pen=2792.41, walk_pen=1881.0, waste_pen=76.17, days=30, status=completed, final_cash=6411.17
  - seed=123: total=-11333, profit=-6314, sat_pen=0.0, rep_pen=2849.6, walk_pen=2080.0, waste_pen=89.49, days=30, status=completed, final_cash=8685.9

### `tourist_season`
  - seed=42: total=-15866, profit=-10822, sat_pen=0.0, rep_pen=2421.71, walk_pen=2407.0, waste_pen=215.28, days=30, status=completed, final_cash=4177.94
  - seed=88: total=-8275, profit=-3510, sat_pen=0.0, rep_pen=2121.54, walk_pen=2594.0, waste_pen=49.27, days=30, status=completed, final_cash=11489.56
  - seed=123: total=-10554, profit=-5512, sat_pen=0.0, rep_pen=2416.56, walk_pen=2541.0, waste_pen=84.65, days=30, status=completed, final_cash=9488.49

### `renovation`
  - seed=42: total=-17101, profit=-13204, sat_pen=0.0, rep_pen=1967.86, walk_pen=1704.0, waste_pen=225.35, days=30, status=completed, final_cash=3795.99
  - seed=88: total=-14516, profit=-11254, sat_pen=0.0, rep_pen=1226.44, walk_pen=1846.0, waste_pen=190.38, days=30, status=completed, final_cash=5746.36
  - seed=123: total=-14372, profit=-10597, sat_pen=0.0, rep_pen=1721.09, walk_pen=1856.0, waste_pen=197.78, days=30, status=completed, final_cash=6402.67

## Observation schema (top-level keys seen)

  active_menu, alerts, cash, cost_breakdown, customer_trend, day, day_of_week, days_remaining, delivery_history, inventory, menu_book, notes, pending_orders, recent_reviews, reputation_band, service_summary, staff_cost_per_person, staff_level, supplier_catalog, tick_budget_ms, weather_forecast, weather_today, yesterday_revenue, yesterday_total_costs

## service_summary keys seen

  avg_wait_minutes, dishes_sold, dishes_unavailable_at, hourly_covers, kitchen_bottleneck_hours, peak_wait_minutes, substitution_count, table_utilization_peak, total_covers, total_revenue, walkout_band

## day_result keys seen

  dishes_sold, substitutions, total_covers, total_revenue, walkout_band