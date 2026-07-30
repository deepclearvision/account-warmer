"""
Mobile Warm-Up — Daily Short Sessions on GeelarK Cloud Phones

Runs one 2–5 minute session per phone.  All sessions are strictly sequential
(one phone at a time) because all 5 phones share a single StreamVia mobile
proxy — starting phones simultaneously would give them all the same IP.

Session flow per phone:
  1. Rotate proxy IP (changeipunique=true) — poll until Ready
  2. Start phone
  3. Wait for boot
  4. Refresh GPS location (city coordinates)
  5. Open Google Maps → search a random nearby place → browse 30s
  6. Open YouTube → browse Shorts or search a topic → watch 30–60s
  7. Open Gmail → scroll inbox
  8. Return to home screen
  9. Stop phone
  10. Log session to WarmingData/logs/mobile_sessions.json

Skips any account with mobile_warming_enabled=false or no phone provisioned.
"""

import json
import logging
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mobile_warmup")

# ── Import ADB helpers ────────────────────────────────────────────────────────
from activities.google_login_mobile import (
    _shell, _find_and_tap, _get_window_focus, _rotate_proxy_ip, _CITY_CONFIG,
    _wait_for_foreground_app, _get_screen_size, _scale,
)

# ── New shared location + permission helper ───────────────────────────────────
from activities.mobile_location_setup import (
    ensure_location_and_permissions,
    set_gps_near_business,
    choose_business_location,
)

LAUNCHER_ACTIVITY = "com.android.launcher3"

# ── Reference screen for scaled taps ──────────────────────────────────────────
_REF_W = 720
_REF_H = 1440

# ── Random content pools ──────────────────────────────────────────────────────

_MAP_SEARCHES = [
    "coffee shops near me",
    "restaurants near me",
    "pharmacies near me",
    "supermarkets near me",
    "gyms near me",
    "parks near me",
    "barbers near me",
    "pizza delivery near me",
    "petrol stations near me",
    "post office near me",
]

# High-commercial-intent keywords for the Money KW batch script.
# Each query signals a user ready to spend money — solicitor, plumber, etc.
_MONEY_KW_POOL = [
    "best plumber near me", "emergency electrician london",
    "cheapest car insurance uk", "same day flower delivery london",
    "roof repair near me", "boiler service london",
    "private dentist near me", "cheap taxi near me",
    "best solicitor near me", "accountant near me",
    "car mechanic near me", "pest control near me",
    "drain unblocking near me", "locksmith 24 hour near me",
    "skip hire near me", "removals company near me",
    "car valeting near me", "conveyancing solicitor london",
    "mortgage broker london", "will writing service near me",
    "private gp london", "physiotherapist near me",
]

# London driving directions pairs — (dest_lat_lon, origin_lat, origin_lon, label)
# dest_lat_lon used in saddr/daddr URL so Maps never falls back to IP/network location.
# Pairs are ~0.5–3 km apart across South/Central/East London.
_DIRECTIONS_PAIRS = [
    # (dest_coords,           origin_lat,  origin_lon,  )
    ("51.5117,-0.1240",       51.5137,    -0.1337,  ),  # Soho → Covent Garden
    ("51.5178,-0.0823",       51.5228,    -0.0782,  ),  # Shoreditch → Liverpool St
    ("51.4613,-0.1156",       51.4618,    -0.1388,  ),  # Clapham → Brixton
    ("51.5309,-0.1233",       51.5390,    -0.1426,  ),  # Camden → King's Cross
    ("51.5142,-0.1878",       51.5085,    -0.1960,  ),  # Notting Hill → Bayswater
    ("51.4826,-0.0077",       51.5054,    -0.0235,  ),  # Canary Wharf → Greenwich
    ("51.5453,-0.0753",       51.5450,    -0.0553,  ),  # Hackney → Dalston
    ("51.5325,-0.1057",       51.5349,    -0.1029,  ),  # Islington → Angel
    ("51.4959,-0.1002",       51.4736,    -0.0694,  ),  # Peckham → Elephant & Castle
    ("51.4875,-0.1687",       51.4739,    -0.1994,  ),  # Fulham → Chelsea
    ("51.5045,-0.2180",       51.4927,    -0.2237,  ),  # Hammersmith → Shepherd's Bush
    ("51.5196,-0.0613",       51.5269,    -0.0545,  ),  # Bethnal Green → Whitechapel
    ("51.4881,-0.1071",       51.4855,    -0.1234,  ),  # Vauxhall → Kennington
    ("51.5014,-0.0898",       51.4995,    -0.0745,  ),  # Bermondsey → Borough Market
    ("51.5568,-0.1368",       51.5657,    -0.1356,  ),  # Archway → Tufnell Park
    ("51.4959,-0.1427",       51.4875,    -0.1687,  ),  # Chelsea → Victoria
    ("51.5269,-0.0545",       51.5196,    -0.0613,  ),  # Whitechapel → Bethnal Green
    ("51.5642,-0.1066",       51.5453,    -0.0753,  ),  # Dalston → Finsbury Park
    ("51.4732,-0.1227",       51.4613,    -0.1156,  ),  # Brixton → Stockwell
    ("51.5054,-0.0235",       51.4826,    -0.0077,  ),  # Greenwich → Canary Wharf
    # Additional routes
    ("51.5074,-0.1278",       51.5014,    -0.0898,  ),  # Bermondsey → Waterloo
    ("51.4893,-0.1441",       51.4959,    -0.1427,  ),  # Chelsea → Pimlico
    ("51.5476,-0.0891",       51.5642,    -0.1066,  ),  # Finsbury Park → Stoke Newington
    ("51.4627,-0.3053",       51.4736,    -0.2994,  ),  # Ealing → Hanwell
    ("51.5400,-0.1426",       51.5309,    -0.1233,  ),  # King's Cross → Euston
    ("51.4982,-0.1749",       51.5045,    -0.2180,  ),  # Hammersmith → Chiswick
    ("51.5155,-0.0775",       51.5228,    -0.0782,  ),  # Spitalfields → Shoreditch
    ("51.4835,-0.1036",       51.4881,    -0.1071,  ),  # Stockwell → Oval
    ("51.5116,-0.1362",       51.5142,    -0.1878,  ),  # Soho → Paddington
    ("51.4765,-0.0544",       51.4732,    -0.0422,  ),  # Deptford → New Cross
    ("51.5800,-0.1756",       51.5752,    -0.1669,  ),  # Hendon → Brent Cross
    ("51.5500,-0.2800",       51.5613,    -0.2988,  ),  # Wembley → Harrow
]

_YOUTUBE_SEARCHES = [
    # ── Food & Cooking ──────────────────────────────────────────────────────
    "easy dinner recipes uk", "how to make sourdough bread",
    "best pasta carbonara recipe", "how to cook a perfect steak",
    "meal prep ideas for the week", "air fryer recipes uk",
    "best fish and chips london", "afternoon tea at the ritz",
    "how to make yorkshire pudding", "best roast dinner recipe",
    "vegan recipes for beginners uk", "jamie oliver 30 minute meals",
    "best curry house near me", "how to make homemade pizza",
    "sunday roast at home", "british bake off recipes",
    "how to make scones", "easy chicken tikka masala",
    "student budget meals uk", "best brunch spots london",
    "how to cook lamb roast", "baking for beginners",
    "slow cooker recipes uk", "best street food london",
    "how to make crumpets", "quick and healthy dinners",
    # ── Football / Premier League ────────────────────────────────────────────
    "premier league highlights today", "manchester united highlights",
    "arsenal highlights today", "chelsea fc latest goals",
    "liverpool fc post match", "tottenham hotspur highlights",
    "man city goals 2024", "premier league table analysis",
    "best premier league goals ever", "champions league highlights",
    "england football highlights", "fantasy premier league tips",
    "match of the day full show", "sky sports premier league",
    "football transfer news uk", "fa cup highlights",
    "championship playoff final", "women's super league highlights",
    "best football skills and tricks", "gary neville analysis",
    "premier league predictions", "england world cup highlights",
    "premier league skills of the week",
    # ── UK News ──────────────────────────────────────────────────────────────
    "uk news today", "bbc news headlines",
    "sky news latest", "prime minister's questions",
    "general election results uk", "cost of living crisis uk",
    "uk weather forecast", "house prices uk 2024",
    "nhs waiting times latest", "uk interest rates explained",
    "london tube strike news", "energy price cap explained",
    "budget 2024 uk analysis", "uk immigration rules change",
    "council tax rise explained",
    # ── Music ────────────────────────────────────────────────────────────────
    "uk top 40 this week", "glastonbury highlights",
    "adele live performance", "ed sheeran new song",
    "radio 1 live lounge", "brits 2024 highlights",
    "best uk grime tracks", "reading festival highlights",
    "classic britpop playlist", "uk garage classics",
    "sam fender live", "arctic monkeys glastonbury",
    "best uk drill 2024", "bbc radio 1 big weekend",
    # ── Gaming ───────────────────────────────────────────────────────────────
    "fifa 24 ultimate team tips", "best ps5 games 2024",
    "call of duty warzone gameplay", "fortnite new season",
    "minecraft build ideas", "gta 6 trailer reaction",
    "best gaming pc build 2024 uk", "xbox game pass best games",
    "elden ring boss guide", "football manager 2024 tips",
    "nintendo switch games 2024", "best mobile games 2024",
    # ── Travel UK ────────────────────────────────────────────────────────────
    "best walking routes near me", "london vlog walking tour",
    "best day trips from london", "lake district travel guide",
    "cornwall road trip uk", "edinburgh travel guide",
    "cotswolds villages tour", "scotland nc500 road trip",
    "best beaches uk", "hidden gems london",
    "things to do in london this weekend", "cheap flights from uk",
    "best holiday destinations 2024", "york travel guide",
    "peak district walks", "brighton day trip",
    # ── How-To / DIY ─────────────────────────────────────────────────────────
    "how to fix a leaking tap uk", "how to bleed a radiator",
    "how to paint a room properly", "how to change a car tyre",
    "how to tile a bathroom", "how to fix a toilet flush",
    "how to unblock a sink drain", "how to put up shelves",
    "how to grout tiles", "diy garden makeover uk",
    "how to wallpaper a room", "how to replace a light switch",
    "how to fix creaking floorboards", "how to insulate a loft uk",
    # ── Comedy ───────────────────────────────────────────────────────────────
    "michael mcintyre full show", "jimmy carr best jokes",
    "british panel shows full episodes", "would i lie to you best bits",
    "taskmaster full episodes", "peter kay stand up",
    "ricky gervais comedy", "8 out of 10 cats best moments",
    "lee mack stand up", "james acaster comedy",
    "funniest british sitcom moments", "derry girls best scenes",
    # ── TV Shows ─────────────────────────────────────────────────────────────
    "peaky blinders best scenes", "the crown season 6",
    "doctor who new episodes", "strictly come dancing highlights",
    "britain's got talent best auditions", "top gear best moments",
    "the great british bake off", "eastenders best moments",
    "coronation street highlights", "love island uk best bits",
    "masterchef uk full episodes", "the apprentice uk boardroom",
    "doctor who best moments",
    # ── Films ────────────────────────────────────────────────────────────────
    "best movies to watch 2024", "james bond best moments",
    "harry potter behind the scenes", "top 10 british films",
    "barbie movie review", "oppenheimer explained",
    "marvel movies ranked", "best netflix series uk",
    "best films on amazon prime uk", "cinema releases this week",
    "classic british comedy films",
    # ── Tech Reviews ─────────────────────────────────────────────────────────
    "iphone 15 pro review uk", "samsung galaxy s24 vs iphone",
    "best laptop for students uk", "best budget phone 2024",
    "best vpn uk 2024", "best broadband deals uk",
    "smart home setup uk", "apple watch review 2024",
    "best wireless earbuds uk", "ps5 vs xbox series x 2024",
    "best 4k tv for gaming", "amazon echo vs google nest",
    "best tablet for drawing uk",
    # ── Finance ──────────────────────────────────────────────────────────────
    "how to save money uk", "best savings account uk 2024",
    "how to invest in stocks uk", "isa explained uk",
    "how to buy your first house uk", "mortgage calculator explained",
    "credit score tips uk", "best credit cards uk",
    "side hustles uk 2024", "how to budget money",
    "state pension explained uk", "self assessment tax tips",
    # ── Fitness ──────────────────────────────────────────────────────────────
    "morning workout routine at home", "5k training plan for beginners",
    "yoga for beginners uk", "hiit workout 20 minutes",
    "best running shoes 2024 uk", "gym workout plan for beginners",
    "how to lose belly fat", "pilates for beginners",
    "stretching routine after running", "strength training at home",
    "park run tips for beginners", "best protein powder uk review",
    # ── Parenting ────────────────────────────────────────────────────────────
    "baby sleep training tips uk", "best baby products 2024 uk",
    "family days out london", "free things to do with kids",
    "parenting tips for toddlers", "child benefit changes uk",
    "best pushchair 2024 uk", "weaning baby first foods uk",
    "school holiday activities", "maternity leave rights uk",
    # ── Cars ─────────────────────────────────────────────────────────────────
    "best used cars 2024 uk", "electric cars 2024 uk",
    "how to pass driving test uk", "car insurance tips uk",
    "mot checklist uk", "best small suv 2024",
    "tesla model 3 review uk", "car cleaning and detailing uk",
    "best first cars for new drivers", "how to check car history uk",
    # ── Shopping Hauls ───────────────────────────────────────────────────────
    "primark haul 2024 uk", "ikea home tour uk",
    "b&m bargains haul", "home bargains shop with me",
    "tk maxx haul uk", "charity shop haul uk",
    "zara haul try on uk", "amazon must haves uk",
    "aldi middle aisle finds", "lidl weekly deals uk",
    # ── Home / Garden ────────────────────────────────────────────────────────
    "garden makeover ideas uk", "best indoor plants uk",
    "home organization ideas", "small bedroom makeover",
    "renovation budget tips uk", "best paint colours for living room",
    "how to grow vegetables uk", "home office setup ideas",
    "cleaning motivation uk", "kitchen organization hacks",
    # ── True Crime ───────────────────────────────────────────────────────────
    "uk true crime documentary", "unsolved uk mysteries",
    "crimewatch uk best moments", "serial killer documentary uk",
    "police bodycam footage uk", "real crime podcast uk",
    "london gangland documentary", "criminal psychology explained",
    "most notorious uk criminals", "disappearance mysteries uk",
    # ── Weather ──────────────────────────────────────────────────────────────
    "uk weather forecast this week", "met office weather warning",
    "london weather today", "snow forecast uk",
    "storm update uk", "heatwave uk 2024",
    "bbc weather 10 day forecast", "best weather app uk",
    # ── Long-tail / UK-specific ──────────────────────────────────────────────
    "how to fix a leaking tap uk", "best walking routes near me",
    "manchester united highlights today",
    "how to get from heathrow to central london",
    "best fish and chips near me uk",
    "how to apply for universal credit uk",
    "passport renewal how long uk 2024",
    "london property market 2024",
    "how to make perfect tea british style",
    "how to complain to ofcom uk",
    "right to buy scheme explained uk",
    "free school meals eligibility uk",
    "how to register with a gp nhs",
    "congestion charge zone map london",
]

_GOOGLE_SEARCHES = [
    # Food & Drink
    "best pizza near me",
    "best indian restaurant near me",
    "best chinese takeaway london",
    "best sushi restaurant london",
    "best burger place london",
    "best ramen london",
    "best sunday roast london",
    "best brunch london",
    "best afternoon tea london",
    "best vegan restaurant london",
    "best coffee shops london",
    "best espresso martini london",
    "best cocktail bars shoreditch",
    "best pub near me",
    "best gastropub london",
    "best fish and chips london",
    "best kebab near me",
    "best curry house near me",
    "best bakery london",
    "best dim sum london",
    "best tapas london",
    "best thai restaurant london",
    "best korean bbq london",
    "best pizza shoreditch",
    "best italian restaurant london",
    "best steak restaurant london",
    "best noodles near me",
    "best fried chicken near me",
    "best pho london",
    "best wine bar london",
    # Shopping
    "what time does sainsbury's close",
    "what time does tesco open",
    "what time does boots close",
    "primark opening times",
    "john lewis nearest to me",
    "marks and spencer near me",
    "waitrose near me",
    "asda near me",
    "lidl near me",
    "aldi near me",
    "ikea london opening times",
    "next clothing sale uk",
    "nike store london",
    "adidas store london",
    "apple store london nearest",
    "currys pc world near me",
    "argos near me opening times",
    "halfords near me",
    "b&q near me",
    "homebase near me",
    "waterstones london",
    "hmv london",
    "tk maxx near me",
    "sports direct near me",
    "decathlon london",
    # Transport
    "how to get to heathrow from central london",
    "tube map pdf",
    "london overground timetable",
    "gatwick express timetable",
    "national rail journey planner",
    "oyster card top up online",
    "how much is a daily travelcard london",
    "elizabeth line stations",
    "dlr map london",
    "bus from victoria to waterloo",
    "cheapest uber alternative london",
    "how long does it take to walk from london bridge to tower bridge",
    "parking near o2 arena",
    "congestion charge zone map",
    "ulez checker",
    "cycle hire near me",
    "night tube map",
    "heathrow terminal 5 airlines",
    "stansted express time",
    "thameslink timetable",
    # Weather & News
    "weather in london this week",
    "weather forecast london 10 days",
    "bbc weather london",
    "uk news today",
    "the guardian latest news",
    "bbc news uk",
    "sky news headlines",
    "times newspaper uk",
    "evening standard london",
    "metro uk news",
    # Things to Do
    "things to do in london this weekend",
    "free things to do in london",
    "museums in london free",
    "london eye ticket prices",
    "tower of london tickets",
    "tate modern opening times",
    "natural history museum london",
    "victoria and albert museum",
    "british museum opening times",
    "science museum london",
    "national gallery london",
    "southbank events this week",
    "barbican events london",
    "comedy club london",
    "escape rooms london",
    "bowling near me",
    "go karting near me london",
    "laser tag near me",
    "trampoline park near me",
    "adventure golf near me",
    "cinemas near me",
    "odeon listings london",
    "vue cinema near me",
    "picturehouse cinema london",
    "theatre shows london",
    "west end shows london",
    "hamilton tickets london",
    "les miserables london",
    "phantom of the opera london",
    "wicked musical london",
    # Health & NHS
    "nhs urgent care near me",
    "nhs 111 online",
    "gp near me accepting patients",
    "dentist near me nhs",
    "optician near me",
    "pharmacy open now near me",
    "walk in centre near me",
    "covid booster where to get it",
    "how to register with a gp",
    "how to get a sick note uk",
    "nhs app how to use",
    "blood test near me",
    "hospital near me",
    # Finance & Admin
    "what is the minimum wage uk 2024",
    "how long does passport renewal take uk",
    "dvla driving licence renewal",
    "how to apply for universal credit",
    "council tax bands london",
    "stamp duty calculator uk",
    "how to check national insurance number",
    "self assessment tax return deadline",
    "hmrc contact number",
    "how to open a bank account uk",
    "best savings account uk 2024",
    "best credit card uk cashback",
    "mortgage calculator uk",
    "help to buy scheme uk",
    "lifetime isa rules",
    "how to claim ppi",
    "energy bills help uk",
    "ofgem price cap",
    # Travel
    "cheap flights to barcelona",
    "cheap flights to amsterdam",
    "cheap flights to rome",
    "cheap flights to dubai",
    "cheap flights to new york",
    "best hotels in paris",
    "airbnb london",
    "booking.com deals",
    "skyscanner cheapest days to fly",
    "easyjet hand luggage allowance",
    "ryanair check in online",
    "british airways seat selection",
    "euro tunnel prices",
    "ferry to france calais",
    "travel insurance uk",
    "esta for usa from uk",
    "do i need a visa for turkey uk passport",
    "spain travel requirements uk",
    "europe travel insurance",
    "best time to visit japan",
    # Sport & Fitness
    "premier league fixtures",
    "premier league table",
    "arsenal fixtures",
    "chelsea fc fixtures",
    "tottenham fixtures",
    "man city results",
    "man united news",
    "liverpool fc latest",
    "championship table",
    "fa cup results",
    "rugby six nations fixtures",
    "wimbledon tickets",
    "formula 1 schedule 2024",
    "golf uk open 2024",
    "boxing fights uk 2024",
    "gym near me",
    "crossfit near me london",
    "swimming pool near me",
    "yoga classes near me",
    "pilates near me",
    "personal trainer london",
    "park run near me",
    "5k training plan for beginners",
    "how to lose weight quickly",
    "best protein powder uk",
    # Home & Garden
    "national trust places near london",
    "garden centre near me",
    "b&q compost",
    "best lawn mower uk 2024",
    "how to fix a leaking tap",
    "best boiler brand uk",
    "how to bleed a radiator",
    "loft conversion cost london",
    "extension cost london",
    "best estate agents london",
    "rightmove london",
    "zoopla london",
    "how much is my house worth",
    "best interior designers london",
    "ikea kitchen planner",
    # Tech & Entertainment
    "how to cancel netflix uk",
    "best vpn uk 2024",
    "iphone 15 review",
    "samsung galaxy s24 vs iphone",
    "best laptop uk 2024",
    "best broadband deals uk",
    "sky tv packages",
    "virgin media deals",
    "spotify student discount",
    "amazon prime day 2024",
    "best smart tv uk",
    "nintendo switch deals uk",
    "ps5 restock uk",
    "xbox game pass games list",
    "best podcast apps uk",
    # Recipes & Cooking
    "how to make sourdough bread",
    "best pasta carbonara recipe",
    "easy chicken curry recipe",
    "how to make naan bread",
    "best chocolate cake recipe uk",
    "how to cook a perfect steak",
    "vegetarian recipes quick",
    "how to make risotto",
    "easy biscuit recipe",
    "how to make yorkshire pudding",
    "beef stew slow cooker recipe",
    "how to make homemade pizza",
    "best chilli con carne recipe",
    "how to make crumpets",
    "fish pie recipe uk",
    # Misc Everyday
    "post office near me opening times",
    "how to send a parcel royal mail",
    "royal mail tracking",
    "hermes parcel tracking",
    "dpd delivery tracking",
    "how to recycle near me",
    "tip near me opening times",
    "dry cleaner near me",
    "car wash near me",
    "petrol station near me cheapest",
    "dog groomer near me",
    "vet near me",
    "locksmith near me 24 hour",
    "electrician near me",
    "plumber near me emergency",
    "cleaning service near me",
    "removal company london",
    "storage unit near me",
    "how to dispute a parking ticket",
    "how to report a pothole uk",
]

# ── Batch-mode schedule scripts ─────────────────────────────────────────────
# These replace the old 5-activity random warmup.  Each runs exactly one
# batch-mode session per day, determined by _day_to_script(schedule_day).

def _day_to_script(day: int) -> str:
    """
    Monthly schedule pattern (days 1+).
      Day  1-2:  local_discovery   (initial warming)
      Day  3:    money_kw          (first business interaction)
      Day  4-10: local_discovery
      Day 11:    brand_1km
      Day 12-20: local_discovery
      Day 21:    brand_1km
      Day 22-30: local_discovery
    After day 30: daily local_discovery + brand_1km every 10th day.
    """
    cycle = ((day - 1) % 30) + 1
    if day <= 30:
        if cycle == 3:
            return "money_kw"
        if cycle in (11, 21):
            return "brand_1km"
        return "local_discovery"
    if cycle % 10 == 0:
        return "brand_1km"
    return "local_discovery"


def _batch_local_discovery(phone_id: str, acc_id: str, account: dict,
                           log_acc_id: str = "") -> bool:
    """
    Everyday local browsing — Maps "near me" search + browse a listing.
    Optionally also do a Chrome search or YouTube watch for variety.
    This is the default bread-and-butter script (runs ~27 days/month).
    """
    log.info("[%s] Local Discovery batch …", acc_id)
    try:
        # Roll for add-on timing BEFORE opening Maps
        #   25% YouTube before  |  35% YouTube after  |  10% Chrome after  |  30% nothing
        roll = random.random()

        # 25% chance: YouTube BEFORE Maps
        if roll < 0.25:
            _warmup_youtube(phone_id, acc_id, account, log_acc_id)
            _press_home(phone_id)

        _open_app(phone_id, "com.google.android.apps.maps", acc_id)

        # Tap My Location (40% chance)
        if random.random() < 0.4:
            time.sleep(random.uniform(1.5, 3.0))
            _shell(phone_id, "input tap 1010 1650")
            time.sleep(random.uniform(1.5, 2.5))

        # Search a random "near me" category
        query = random.choice(_MAP_SEARCHES)
        tapped = _find_and_tap(phone_id, ["Search here", "Search Google Maps",
                                          "Search Maps"])
        if not tapped:
            log.warning("[%s] Local Discovery: Maps search bar not found", acc_id)
            _press_home(phone_id)
            return False
        time.sleep(2)
        _type_and_search(phone_id, query)
        time.sleep(4)
        _swipe_down(phone_id)
        time.sleep(2)

        # Open a listing — same two-step pattern as _warmup_maps
        opened = _find_and_tap(phone_id, ["Open", "Closed", "km", "m away", "·"])
        time.sleep(3)
        if not opened:
            _shell(phone_id, "input tap 540 850")
            time.sleep(3)
        detail = _find_and_tap(phone_id, ["Directions", "Call", "Website",
                                          "Save", "Share"])
        if detail:
            dwell = random.randint(20, 40)
            scrolls = dwell // 8
            for _ in range(scrolls):
                _swipe_down(phone_id)
                time.sleep(random.uniform(2.5, 5.0))
            _shell(phone_id, "input swipe 540 400 540 1200 500")
            time.sleep(2)
        else:
            time.sleep(random.randint(15, 25))

        # Exit Maps before any after-addon
        _press_home(phone_id)

        # 35% chance: YouTube AFTER Maps
        if roll >= 0.25 and roll < 0.60:
            _warmup_youtube(phone_id, acc_id, account, log_acc_id)
        # 10% chance: Chrome search AFTER Maps
        elif roll >= 0.60 and roll < 0.70:
            _do_quick_chrome_search(phone_id, acc_id, log_acc_id)
        # else (roll >= 0.70): no extra activity

        _press_home(phone_id)
        log.info("[%s] Local Discovery done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Local Discovery failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


def _do_quick_chrome_search(phone_id: str, acc_id: str,
                            log_acc_id: str = "") -> None:
    """Quick Google search in Chrome — used as an add-on within batch scripts."""
    try:
        _shell(phone_id, "am start -a android.intent.action.VIEW "
               "-d 'https://www.google.co.uk' com.android.chrome")
        _wait_for_foreground_app(phone_id, "chrome", timeout=15, acc_id=acc_id)
        # Dismiss Chrome first-run dialogs
        _find_and_tap(phone_id, ["Accept & continue", "Got it"])
        time.sleep(0.5)
        _find_and_tap(phone_id, ["No thanks", "Skip", "Later", "Not now"])
        time.sleep(2)

        query = random.choice(_GOOGLE_SEARCHES[:50])  # first 50 are local/nearby
        _find_and_tap(phone_id, ["Search or type web address",
                                 "Search or type URL",
                                 "Search or type a URL",
                                 "Search web"])
        time.sleep(1)
        _type_and_search(phone_id, query)
        time.sleep(random.randint(8, 15))
        _press_home(phone_id)
    except Exception:
        _press_home(phone_id)


def _batch_money_kw(phone_id: str, acc_id: str, account: dict,
                    log_acc_id: str = "") -> bool:
    """
    High-commercial-intent keyword searches — signals the account is ready to
    spend money. Two Google searches on a money keyword, clicking an organic
    result, scrolling the landing page. Runs once in month 1 (day 3).
    """
    log.info("[%s] Money KW batch …", acc_id)
    from core.activity_log import log_event
    try:
        _shell(phone_id, "am start -a android.intent.action.VIEW "
               "-d 'https://www.google.co.uk' com.android.chrome")
        _wait_for_foreground_app(phone_id, "chrome", timeout=15, acc_id=acc_id)
        _find_and_tap(phone_id, ["Accept & continue", "Got it"])
        time.sleep(0.5)
        _find_and_tap(phone_id, ["No thanks", "Skip", "Later", "Not now"])
        time.sleep(2)

        keywords_used = []
        for _ in range(2):
            kw = random.choice(_MONEY_KW_POOL)
            keywords_used.append(kw)
            log.info("[%s] Money KW search: %s", acc_id, kw)
            _find_and_tap(phone_id, ["Search or type web address",
                                     "Search or type URL",
                                     "Search or type a URL",
                                     "Search web"])
            time.sleep(1)
            _type_and_search(phone_id, kw)
            time.sleep(4)
            # Tap first organic result (rough coordinates below the ad section)
            _shell(phone_id, "input tap 540 650")
            time.sleep(random.randint(8, 15))
            # Back to results
            _press_back(phone_id, times=1)
            time.sleep(2)

        if log_acc_id:
            try:
                log_event(log_acc_id, "mobile", "money_kw",
                          {"keywords": keywords_used})
            except Exception:
                pass

        # Randomised follow-up after money-keyword searches:
        #   50% YouTube  |  20% Chrome  |  30% nothing
        follow_up = random.random()
        _press_home(phone_id)
        if follow_up < 0.50:
            _warmup_youtube(phone_id, acc_id, account, log_acc_id)
        elif follow_up < 0.70:
            _do_quick_chrome_search(phone_id, acc_id, log_acc_id)

        _press_home(phone_id)
        log.info("[%s] Money KW done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Money KW failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


def _batch_brand_1km(phone_id: str, acc_id: str, account: dict,
                     log_acc_id: str = "") -> bool:
    """
    Navigate to a specific named business in Maps — simulates a customer who
    knows exactly what business they want. Searches by name, opens directions,
    browses the listing. Runs twice in month 1 (days 11 and 21).
    """
    log.info("[%s] Brand 1km batch …", acc_id)
    from core.activity_log import log_event
    try:
        # ── YouTube roll BEFORE Maps ──────────────────────────────────────
        yt_roll = random.random()   # 30% before, 40% after, 30% none

        if yt_roll < 0.30:
            _warmup_youtube(phone_id, acc_id, account, log_acc_id)
            _press_home(phone_id)

        # ── GPS variation: vary the GPS base each brand_1km run ────────────
        gps_roll = random.random()
        from set_phone_area import set_phone_gps
        if gps_roll < 0.40 and account.get("home_lat") and account.get("home_lng"):
            # 40%: set GPS to home with jitter
            lat = account["home_lat"] + random.uniform(-0.0005, 0.0005)
            lon = account["home_lng"] + random.uniform(-0.0007, 0.0007)
            set_phone_gps(phone_id, lat, lon)
            log.info("[%s] Brand 1km GPS: home (%.4f, %.4f)", acc_id, lat, lon)
        elif gps_roll < 0.65 and account.get("work_lat") and account.get("work_lng"):
            # 25%: set GPS to work with jitter
            lat = account["work_lat"] + random.uniform(-0.0005, 0.0005)
            lon = account["work_lng"] + random.uniform(-0.0007, 0.0007)
            set_phone_gps(phone_id, lat, lon)
            log.info("[%s] Brand 1km GPS: work (%.4f, %.4f)", acc_id, lat, lon)
        elif gps_roll < 0.90:
            # 25%: set GPS near target business
            biz = choose_business_location(account)
            if biz:
                set_gps_near_business(phone_id, account, biz, acc_id, jitter_meters=200)
            else:
                ensure_location_and_permissions(phone_id, account, acc_id)
        else:
            # 10%: keep area/city fallback
            ensure_location_and_permissions(phone_id, account, acc_id)
            log.info("[%s] Brand 1km GPS: area/city fallback", acc_id)

        _open_app(phone_id, "com.google.android.apps.maps", acc_id)

        # Resolve target business — priority: account's target_businesses,
        # then a random business from businesses.yaml, then a well-known chain
        biz_name, biz_lat, biz_lng = _resolve_target_business(account)

        tapped = _find_and_tap(phone_id, ["Search here", "Search Google Maps",
                                          "Search Maps"])
        if not tapped:
            log.warning("[%s] Brand 1km: Maps search bar not found", acc_id)
            _press_home(phone_id)
            return False
        time.sleep(2)

        log.info("[%s] Brand 1km search: %s", acc_id, biz_name)
        _type_and_search(phone_id, biz_name)
        time.sleep(4)
        _swipe_down(phone_id)
        time.sleep(2)

        # Open the listing
        opened = _find_and_tap(phone_id, ["Open", "Closed", "km", "m away", "·"])
        time.sleep(3)
        if not opened:
            _shell(phone_id, "input tap 540 850")
            time.sleep(3)

        # 50% chance: get directions
        if random.random() < 0.5:
            _find_and_tap(phone_id, ["Directions"])
            time.sleep(random.randint(8, 15))

        # Browse the detail card
        detail = _find_and_tap(phone_id, ["Directions", "Call", "Website",
                                          "Save", "Share"])
        if detail:
            dwell = random.randint(30, 60)
            scrolls = dwell // 8
            for _ in range(scrolls):
                _swipe_down(phone_id)
                time.sleep(random.uniform(2.5, 5.0))
            _shell(phone_id, "input swipe 540 400 540 1200 500")
            time.sleep(2)
        else:
            time.sleep(random.randint(15, 25))

        if log_acc_id:
            try:
                log_event(log_acc_id, "mobile", "brand_1km",
                          {"business_name": biz_name})
            except Exception:
                pass

        # 40% chance: YouTube AFTER Maps
        _press_home(phone_id)
        if yt_roll >= 0.30 and yt_roll < 0.70:
            _warmup_youtube(phone_id, acc_id, account, log_acc_id)

        _press_home(phone_id)
        log.info("[%s] Brand 1km done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Brand 1km failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


def _resolve_target_business(account: dict) -> tuple:
    """Resolve a target business (name, lat, lng) for brand navigations."""
    import yaml
    from core.paths import DATA_DIR

    # Priority 1: account's target_businesses list
    biz_ids = account.get("target_businesses", [])
    if biz_ids:
        try:
            biz_file = DATA_DIR / "businesses.yaml"
            if biz_file.exists():
                data = yaml.safe_load(biz_file.read_text(encoding="utf-8")) or {}
                for b in data.get("businesses", []):
                    if b.get("id") in biz_ids and b.get("lat") and b.get("lng"):
                        return b.get("name"), b.get("lat"), b.get("lng")
        except Exception:
            pass

    # Priority 2: random business from businesses.yaml with lat/lng
    try:
        biz_file = DATA_DIR / "businesses.yaml"
        if biz_file.exists():
            data = yaml.safe_load(biz_file.read_text(encoding="utf-8")) or {}
            candidates = [b for b in data.get("businesses", [])
                         if b.get("lat") and b.get("lng")]
            if candidates:
                b = random.choice(candidates)
                return b.get("name"), b.get("lat"), b.get("lng")
    except Exception:
        pass

    # Priority 3: well-known UK retail chain for generic interaction
    chains = [
        "Tesco", "Sainsbury's", "Waitrose", "Boots", "Costa Coffee",
        "Greggs", "Pizza Express", "Nando's", "Wetherspoons",
        "Pret a Manger", "Starbucks", "McDonald's",
    ]
    return random.choice(chains), None, None


# ── ADB helpers ───────────────────────────────────────────────────────────────

def _press_home(phone_id: str) -> None:
    _shell(phone_id, "input keyevent KEYCODE_HOME")
    time.sleep(2)


def _open_app(phone_id: str, package: str, acc_id: str = "") -> None:
    _shell(phone_id, f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    _wait_for_foreground_app(phone_id, package, timeout=12, acc_id=acc_id)


def _swipe_down(phone_id: str) -> None:
    _shell(phone_id, "input swipe 540 400 540 1200 700")
    time.sleep(1)


def _press_back(phone_id: str, times: int = 1) -> None:
    for _ in range(times):
        _shell(phone_id, "input keyevent KEYCODE_BACK")
        time.sleep(1)


def _type_and_search(phone_id: str, text: str) -> None:
    """Type into the currently-focused search box and press Enter."""
    escaped = text.replace(" ", "%s")
    _shell(phone_id, f"input text {escaped}")
    time.sleep(0.8)
    _shell(phone_id, "input keyevent 66")  # Enter
    time.sleep(3)


def _refresh_gps(phone_id: str, account: dict, acc_id: str) -> None:
    """
    Set GPS location via mock-GPS broadcast (reliable) or GeelarK API fallback.

    Priority order:
      1. home_lat / home_lng from account (set by generate-home-addresses) — most precise
      2. geo_area from AREAS dict (area-centre with random jitter)
      3. geo_city fallback

    A small jitter is always applied so exact coordinates vary each session.
    """
    from set_phone_area import AREAS, area_coords, set_phone_gps

    # Priority 1: home or work coords (65% home / 35% work when both available)
    home_lat = account.get("home_lat")
    home_lng = account.get("home_lng")
    work_lat = account.get("work_lat")
    work_lng = account.get("work_lng")

    use_work = work_lat and work_lng and random.random() < 0.35
    if use_work:
        base_lat, base_lng, loc_label = work_lat, work_lng, "work"
    elif home_lat and home_lng:
        base_lat, base_lng, loc_label = home_lat, home_lng, "home"
    else:
        base_lat = base_lng = None

    if base_lat and base_lng:
        lat = base_lat + random.uniform(-0.0005, 0.0005)
        lon = base_lng + random.uniform(-0.0007, 0.0007)
        ok = set_phone_gps(phone_id, lat, lon)
        if ok:
            log.info("[%s] GPS set to %s location (%.4f, %.4f)", acc_id, loc_label, lat, lon)
            return
        log.warning("[%s] GPS set failed for %s coords, trying geo_area", acc_id, loc_label)

    # Priority 2: geo_area centre with area-radius jitter
    area_key = account.get("geo_area")
    if area_key and area_key in AREAS:
        lat, lon = area_coords(area_key, jitter=True)
        name = AREAS[area_key][0]
        ok = set_phone_gps(phone_id, lat, lon)
        if ok:
            log.info("[%s] GPS set via area: %s (%.4f, %.4f)", acc_id, name, lat, lon)
            return
        log.warning("[%s] GPS set failed for area, falling back to city", acc_id)

    # Fallback: city-level coords via broadcast/API
    geo_city = (account.get("geo_city") or "london").lower()
    cfg = _CITY_CONFIG.get(geo_city, _CITY_CONFIG["london"])
    _, lat, lon = cfg
    ok = set_phone_gps(phone_id, lat, lon)
    if ok:
        log.info("[%s] GPS set via city fallback: lat=%.4f lon=%.4f (city=%s)", acc_id, lat, lon, geo_city)
    else:
        log.warning("[%s] GPS set completely failed (city=%s)", acc_id, geo_city)


# ── Route cache — avoids re-querying OSRM for the same pair ──────────────────
_route_cache: dict[str, list[tuple[float, float]]] = {}


def _fetch_route_waypoints(
    origin_lat: float, origin_lon: float,
    dest_lat: float,   dest_lon: float,
) -> list[tuple[float, float]]:
    """
    Fetch road-following waypoints from the OSRM public routing API.

    Returns a list of (lat, lon) tuples that trace actual London roads between
    origin and destination.  Results are cached in-process so repeated sessions
    on the same route pair don't make redundant HTTP calls.

    Falls back to an empty list on any network/parse error — callers should
    handle that by falling back to linear interpolation.
    """
    import requests

    # Round to 4 dp for cache key (~11 m precision — plenty for our jittered origins)
    cache_key = f"{origin_lat:.4f},{origin_lon:.4f};{dest_lat:.4f},{dest_lon:.4f}"
    if cache_key in _route_cache:
        return _route_cache[cache_key]

    # OSRM expects coordinates as lon,lat (not lat,lon)
    url = (
        f"http://router.project-osrm.org/route/v1/driving/"
        f"{origin_lon:.6f},{origin_lat:.6f};{dest_lon:.6f},{dest_lat:.6f}"
        f"?geometries=geojson&overview=full"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # GeoJSON coordinates are [lon, lat] — swap to (lat, lon)
        raw = data["routes"][0]["geometry"]["coordinates"]
        waypoints = [(round(lat, 6), round(lon, 6)) for lon, lat in raw]
        _route_cache[cache_key] = waypoints
        log.info("OSRM route fetched: %d waypoints for %s", len(waypoints), cache_key)
        return waypoints
    except Exception as exc:
        log.warning("OSRM fetch failed (%s) — will use linear fallback", exc)
        return []


def _sample_waypoints(
    waypoints: list[tuple[float, float]], n: int
) -> list[tuple[float, float]]:
    """Return n evenly-spaced points sampled from a waypoint list."""
    if len(waypoints) <= n:
        return waypoints
    indices = [int(round(i * (len(waypoints) - 1) / (n - 1))) for i in range(n)]
    return [waypoints[i] for i in indices]


def _animate_gps_drive(
    phone_id: str,
    acc_id: str,
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    steps: int = 12,
) -> None:
    """
    Animate GPS along real London roads from origin to destination.

    Queries the OSRM public routing API for the actual road geometry, samples
    `steps` evenly-spaced points from the route polyline, and pushes a GPS
    update at each one via the mock-GPS broadcast — mimicking a genuine car
    journey through London streets.

    A tiny GPS jitter (±2 m) is applied at each step so the trace doesn't look
    pixel-perfect.  Falls back to linear interpolation if OSRM is unavailable.

    Timing: steps × 4–7 s  ≈  50–85 s total.
    """
    from core.gps_spoofing import drive_route

    # Try to get road-following waypoints from OSRM
    waypoints = _fetch_route_waypoints(origin_lat, origin_lon, dest_lat, dest_lon)

    if waypoints:
        sampled = _sample_waypoints(waypoints, steps + 1)
        log.info("[%s] GPS road drive: %d road waypoints → sampling %d steps",
                 acc_id, len(waypoints), len(sampled))
    else:
        # Linear fallback — straight line with jitter
        log.info("[%s] GPS drive (linear fallback): (%.4f,%.4f) → (%.4f,%.4f)",
                 acc_id, origin_lat, origin_lon, dest_lat, dest_lon)
        sampled = [
            (
                origin_lat + (dest_lat - origin_lat) * i / steps,
                origin_lon + (dest_lon - origin_lon) * i / steps,
            )
            for i in range(steps + 1)
        ]

    # Add jitter to each sampled point
    jittered = [
        (
            lat + random.uniform(-0.00002, 0.00002),
            lon + random.uniform(-0.00003, 0.00003),
        )
        for lat, lon in sampled
    ]

    drive_route(phone_id, jittered, pause_range=(4.0, 7.0))
    log.info("[%s] GPS arrived at destination (%.4f, %.4f)", acc_id, dest_lat, dest_lon)


# ── Warm-up activities ────────────────────────────────────────────────────────

def _warmup_maps(phone_id: str, acc_id: str, account: dict,
                 log_acc_id: str = "") -> bool:
    """Open Maps, search a nearby place, open its detail card, scroll reviews/photos."""
    log.info("[%s] Maps warm-up …", acc_id)
    try:
        # ── Shared location + permissions setup ────────────────────────────
        ensure_location_and_permissions(phone_id, account, acc_id)

        _open_app(phone_id, "com.google.android.apps.maps", acc_id)

        # Vary "tap My Location" probability (30–50%)
        if random.random() < random.uniform(0.30, 0.50):
            time.sleep(random.uniform(1.5, 3.0))  # brief pause before tapping
            _shell(phone_id, "input tap 1010 1650")
            log.info("[%s] Maps: tapped My Location button", acc_id)
            time.sleep(random.uniform(1.5, 2.5))

        # Tap search bar
        tapped = _find_and_tap(phone_id, ["Search here", "Search Google Maps", "Search Maps"])
        if not tapped:
            log.warning("[%s] Maps: search bar not found", acc_id)
            _press_home(phone_id)
            return False
        time.sleep(random.uniform(1.5, 3.0))

        # Type a random search
        query = random.choice(_MAP_SEARCHES)
        log.info("[%s] Maps search: %s", acc_id, query)
        if log_acc_id:
            try:
                from core.activity_log import log_event
                log_event(log_acc_id, "mobile", "maps_browse", {"search_term": query})
            except Exception:
                pass
        _type_and_search(phone_id, query)
        time.sleep(random.uniform(3.0, 5.0))

        # Swipe up to reveal results bottom sheet
        _swipe_down(phone_id)
        time.sleep(random.uniform(1.5, 3.0))

        # ── STEP 1: Tap the first search result ────────────────────────────
        opened_listing = _find_and_tap(phone_id, [
            "Open", "Closed",
            "km", "m away", "mi away",
            "·",
        ])
        time.sleep(3)
        if not opened_listing:
            log.info("[%s] Maps: no text match for result — trying coordinate fallback", acc_id)
            _shell(phone_id, "input tap 540 850")
            time.sleep(3)

        # ── STEP 2: Verify we're on a listing detail card ──────────────────
        detail_opened = _find_and_tap(phone_id, [
            "Directions", "Call", "Website", "Save", "Share",
        ])
        if not detail_opened:
            log.info("[%s] Maps: listing opened but detail labels not visible — short dwell", acc_id)
            time.sleep(random.randint(10, 25))

        # ── STEP 3: Browse the listing with variable behaviour ─────────────
        if detail_opened:
            # Randomly choose browse mode: full scroll (70%) or quick peek (30%)
            if random.random() < 0.70:
                dwell = random.randint(18, 50)
                log.info("[%s] Browsing place detail for %ds", acc_id, dwell)
                scrolls = random.randint(max(1, dwell // 10), max(2, dwell // 6))
                for _ in range(scrolls):
                    _swipe_down(phone_id)
                    time.sleep(random.uniform(2.0, 5.5))
                # Scroll back up naturally (or skip 20% of the time)
                if random.random() < 0.80:
                    _shell(phone_id, "input swipe 540 400 540 1200 500")
                    time.sleep(2)
            else:
                # Quick peek — just scroll once or twice
                quick_scrolls = random.randint(0, 2)
                for _ in range(quick_scrolls):
                    _swipe_down(phone_id)
                    time.sleep(random.uniform(2.0, 4.0))
                time.sleep(random.randint(5, 12))

        # ── STEP 4: Guaranteed interaction check ───────────────────────────
        if not opened_listing and not detail_opened:
            log.warning("[%s] Maps: no listing interaction after all attempts — forced dwell", acc_id)
            _shell(phone_id, "input tap 540 650")
            time.sleep(random.randint(10, 25))

        _press_home(phone_id)
        log.info("[%s] Maps warm-up done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Maps warm-up failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


def _wait_for_phone_ready(phone_id: str, acc_id: str, timeout: int = 60) -> bool:
    """
    Poll until the phone accepts shell commands (i.e. fully booted).

    Uses adaptive intervals — starts at 1 s to catch fast booters, backs off
    to 5 s once the phone has been slow for a while.  Replaces the fixed 5 s
    grid which wastes 4 s on every attempt for phones that boot in 8–12 s.
    """
    # Adaptive intervals: 1 s × 5, then 3 s × 5, then 5 s for the remainder
    intervals = [1, 1, 1, 1, 1, 3, 3, 3, 3, 3]
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        ok, _ = _shell(phone_id, "echo ready")
        if ok:
            return True
        interval = intervals[attempt] if attempt < len(intervals) else 5
        attempt += 1
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
    log.warning("[%s] Phone did not become ready within %ds", acc_id, timeout)
    return False


def _warmup_maps_directions(phone_id: str, acc_id: str, account: dict | None = None,
                             log_acc_id: str = "") -> bool:
    """
    Open a ~1-min driving route in Google Maps navigation.

    Route selection priority:
      1. home/work → associated business (if account has target_businesses with lat/lng)
      2. Random London pair from _DIRECTIONS_PAIRS fallback
    """
    log.info("[%s] Maps directions warm-up ...", acc_id)
    try:
        # ── Shared location + permissions setup ────────────────────────────
        if account:
            ensure_location_and_permissions(phone_id, account, acc_id)

        if not _wait_for_phone_ready(phone_id, acc_id, timeout=60):
            log.warning("[%s] Phone not ready — skipping directions", acc_id)
            return False

        dest_coords = None
        origin_lat = origin_lon = None
        route_label = "random"
        dest_biz_name = None  # set when routing to a named business

        # Try business routing via shared helper
        if account:
            biz = choose_business_location(account)
            if biz and random.random() < 0.60:
                set_gps_near_business(phone_id, account, biz, acc_id, jitter_meters=200)
                dest_lat = biz["lat"]
                dest_lon = biz["lng"]
                dest_coords = f"{dest_lat},{dest_lon}"
                dest_biz_name = biz.get("name")

                # Origin: work (35%) or home (65%)
                use_work = (account.get("work_lat") and account.get("work_lng")
                            and random.random() < 0.35)
                if use_work:
                    origin_lat = account["work_lat"] + random.uniform(-0.0003, 0.0003)
                    origin_lon = account["work_lng"] + random.uniform(-0.0005, 0.0005)
                    route_label = f"work → {dest_biz_name or dest_coords}"
                elif account.get("home_lat") and account.get("home_lng"):
                    origin_lat = account["home_lat"] + random.uniform(-0.0003, 0.0003)
                    origin_lon = account["home_lng"] + random.uniform(-0.0005, 0.0005)
                    route_label = f"home → {dest_biz_name or dest_coords}"
                else:
                    # Fall back to area coords for origin
                    from set_phone_area import AREAS, area_coords as _area_coords
                    area_key = account.get("geo_area")
                    if area_key and area_key in AREAS:
                        origin_lat, origin_lon = _area_coords(area_key, jitter=True)
                    else:
                        origin_lat, origin_lon = 51.5074, -0.1278  # London centre

        # Fallback to random London pair
        if not dest_coords:
            dest_coords, origin_lat, origin_lon = random.choice(_DIRECTIONS_PAIRS)

        # Parse destination lat/lon for GPS animation
        dest_parts = dest_coords.split(",")
        dest_lat = float(dest_parts[0])
        dest_lon = float(dest_parts[1])

        if log_acc_id:
            try:
                from core.activity_log import log_event
                log_event(log_acc_id, "mobile", "maps_directions", {
                    "route": route_label,
                    "destination": dest_biz_name or dest_coords,
                    "dest_lat": dest_lat,
                    "dest_lng": dest_lon,
                })
            except Exception:
                pass

        # Set GPS to origin before opening Maps so the starting pin is accurate
        from set_phone_area import set_phone_gps
        set_phone_gps(phone_id, round(origin_lat, 6), round(origin_lon, 6))
        time.sleep(1)

        _shell(phone_id, "am force-stop com.google.android.apps.maps")
        time.sleep(2)

        nav_uri = (
            f"https://maps.google.com/maps"
            f"?saddr={origin_lat},{origin_lon}"
            f"&daddr={dest_coords}"
            f"&dirflg=d"
        )
        _shell(phone_id,
               f"am start -a android.intent.action.VIEW "
               f"-d \"{nav_uri}\" "
               f"com.google.android.apps.maps")
        _wait_for_foreground_app(phone_id, "com.google.android.apps.maps", timeout=12, acc_id=acc_id)

        # Animate GPS along the route in the background while Maps is visible
        anim_thread = threading.Thread(
            target=_animate_gps_drive,
            args=(phone_id, acc_id, origin_lat, origin_lon, dest_lat, dest_lon),
            daemon=True,
        )
        anim_thread.start()

        dwell = random.randint(40, 95)
        log.info("[%s] Viewing route (%s) for %ds ...", acc_id, route_label, dwell)
        time.sleep(dwell)

        # Wait for animation to finish (it takes ~35–55 s; usually done by now)
        anim_thread.join(timeout=90)

        # Hold GPS at destination and actively browse Maps — stacks Location History
        # visit with real app engagement at those coordinates.
        dest_dwell = random.randint(50, 100)
        log.info("[%s] GPS at destination — opening Maps to browse for %ds", acc_id, dest_dwell)
        try:
            _shell(phone_id, "am force-stop com.google.android.apps.maps")
            time.sleep(1)
            if dest_biz_name:
                # Search for the business by name — strongest signal (GPS + Maps search)
                search_query = dest_biz_name.replace(" ", "%s")
                _shell(phone_id,
                       f"am start -a android.intent.action.VIEW "
                       f"-d \"geo:{dest_lat},{dest_lon}?q={search_query}\" "
                       f"com.google.android.apps.maps")
                log.info("[%s] Maps: searching for business '%s' at destination", acc_id, dest_biz_name)
            else:
                # No named business — open Maps centred on destination coords
                _shell(phone_id,
                       f"am start -a android.intent.action.VIEW "
                       f"-d \"geo:{dest_lat},{dest_lon}?z=16\" "
                       f"com.google.android.apps.maps")
            _wait_for_foreground_app(phone_id, "com.google.android.apps.maps", timeout=10, acc_id=acc_id)
            time.sleep(3)

            # Variable scroll/browse behaviour for the dwell period
            elapsed = 4
            if random.random() < 0.65:
                # Active browsing: regular scrolls
                while elapsed < dest_dwell:
                    _swipe_down(phone_id)
                    pause = random.uniform(3.0, 9.0)
                    time.sleep(pause)
                    elapsed += pause + 0.5
            else:
                # Passive browsing: fewer scrolls, mostly dwell
                scrolls = random.randint(1, 3)
                for _ in range(scrolls):
                    _swipe_down(phone_id)
                    time.sleep(random.uniform(4.0, 8.0))
                time.sleep(max(0, dest_dwell - elapsed - scrolls * 5))
        except Exception as e:
            log.warning("[%s] Destination Maps browse failed: %s — falling back to sleep", acc_id, e)
            time.sleep(max(0, dest_dwell - 5))

        # Return GPS to origin (home/work) before next activity
        set_phone_gps(phone_id, round(origin_lat, 6), round(origin_lon, 6))
        time.sleep(1)

        _press_home(phone_id)
        log.info("[%s] Maps directions warm-up done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Maps directions warm-up failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


def _warmup_youtube(phone_id: str, acc_id: str, account: dict,
                    log_acc_id: str = "") -> bool:
    """
    Open YouTube, search or browse, watch a video with human-like behaviour.

    Flow:
      1. Ensure GPS / location permissions
      2. Open YouTube and wait for foreground
      3. 60% search, 40% browse home/Shorts feed
      4. Watch with realistic mid-watch interactions (taps, pause, comments, like)
      5. Return to home screen
    """
    log.info("[%s] YouTube warm-up …", acc_id)
    try:
        # ── Shared location + permissions setup ────────────────────────────
        ensure_location_and_permissions(phone_id, account, acc_id)

        _open_app(phone_id, "com.google.android.youtube", acc_id)

        # Get screen size for scaled taps
        try:
            screen_w, screen_h = _get_screen_size(phone_id)
        except Exception:
            screen_w, screen_h = _REF_W, _REF_H

        # ── Choose mode: search (60%) or browse home feed (40%) ────────────
        if random.random() < 0.60:
            # ── SEARCH MODE ────────────────────────────────────────────────
            tapped = _find_and_tap(phone_id, ["Search", "Search YouTube"])
            if not tapped:
                # Fallback: tap top-centre where search icon lives
                tx, ty = _scale(0.75, 0.125, screen_w, screen_h)
                _shell(phone_id, f"input tap {tx} {ty}")
            time.sleep(random.uniform(1.5, 3.0))

            query = random.choice(_YOUTUBE_SEARCHES)
            log.info("[%s] YouTube search: %s", acc_id, query)
            if log_acc_id:
                try:
                    from core.activity_log import log_event
                    log_event(log_acc_id, "mobile", "youtube", {"search_term": query})
                except Exception:
                    pass
            _type_and_search(phone_id, query)
            time.sleep(random.uniform(3.0, 5.0))

            # Tap a video result — prefer regular videos over Shorts
            tapped_video = _find_and_tap(phone_id, ["views", "ago", "Watch"])
            if not tapped_video:
                # Fallback: tap a random vertical position in the results area
                ref_y = random.randint(480, 1100)
                fy = ref_y / _REF_H
                ty = _scale(0.50, fy, screen_w, screen_h)[1]
                _shell(phone_id, f"input tap {screen_w // 2} {ty}")
            time.sleep(random.uniform(3.0, 5.0))
        else:
            # ── BROWSE MODE — scroll home feed or Shorts ───────────────────
            scrolls = random.randint(1, 3)
            for _ in range(scrolls):
                _swipe_down(phone_id)
                time.sleep(random.uniform(2.0, 4.0))

            # Tap a thumbnail at a random vertical position
            ref_y = random.randint(500, 1100)
            fy = ref_y / _REF_H
            ty = _scale(0.50, fy, screen_w, screen_h)[1]
            _shell(phone_id, f"input tap {screen_w // 2} {ty}")
            log.info("[%s] YouTube: tapped video at y≈%d (browse mode)", acc_id, ty)
            time.sleep(3)

        # ── WATCH BEHAVIOUR (the key humanization) ─────────────────────────
        watch_time = random.uniform(25, 120)
        log.info("[%s] YouTube: watching for ~%ds", acc_id, int(watch_time))
        elapsed = 0.0
        third = watch_time / 3.0
        two_thirds = 2.0 * watch_time / 3.0

        # First segment (0 → ~1/3)
        seg1 = third + random.uniform(-5, 5)
        seg1 = max(5, seg1)
        time.sleep(seg1)
        elapsed += seg1

        # Mid-watch interaction 1: tap screen to show controls (~always)
        _shell(phone_id, "input tap 540 960")
        time.sleep(random.uniform(1.5, 3.0))
        elapsed += 2.0

        # 30% chance: pause → wait → resume
        if random.random() < 0.30:
            _shell(phone_id, "input tap 540 960")  # tap centre → pause button appears
            time.sleep(0.8)
            # Pause button area on 720×1440: roughly (270, 960) for 1080-wide its centre-ish
            pause_x, pause_y = _scale(0.50, 0.667, screen_w, screen_h)
            _shell(phone_id, f"input tap {pause_x} {pause_y}")
            pause_dur = random.uniform(3, 8)
            log.info("[%s] YouTube: paused for %.0fs", acc_id, pause_dur)
            time.sleep(pause_dur)
            elapsed += pause_dur + 1.0
            # Resume — same tap position
            _shell(phone_id, f"input tap {pause_x} {pause_y}")
            time.sleep(1.0)
            elapsed += 1.0

        # 20% chance: scroll down to peek at comments
        if random.random() < 0.20:
            comment_dur = random.uniform(3, 6)
            log.info("[%s] YouTube: peeking at comments for %.0fs", acc_id, comment_dur)
            _swipe_down(phone_id)
            time.sleep(comment_dur)
            # Scroll back up
            _shell(phone_id, "input swipe 540 400 540 1200 500")
            time.sleep(2)
            elapsed += comment_dur + 3.0

        # Second segment (to ~2/3) — account for mid-watch time already spent
        remaining = two_thirds - elapsed
        if remaining > 5:
            time.sleep(remaining)
            elapsed += remaining

        # Mid-watch interaction 2: tap screen again
        _shell(phone_id, "input tap 540 960")
        time.sleep(random.uniform(1.5, 3.0))
        elapsed += 2.0

        # 10% chance: tap like button area
        if random.random() < 0.10:
            lx, ly = _scale(1010 / _REF_W, 1340 / _REF_H, screen_w, screen_h)
            _shell(phone_id, f"input tap {lx} {ly}")
            log.info("[%s] YouTube: tapped like button area", acc_id)
            time.sleep(1.0)
            elapsed += 1.0

        # 5% chance: tap subscribe button area (only if visible, below video)
        if random.random() < 0.05:
            sx, sy = _scale(620 / _REF_W, 1340 / _REF_H, screen_w, screen_h)
            _shell(phone_id, f"input tap {sx} {sy}")
            log.info("[%s] YouTube: tapped subscribe button area", acc_id)
            time.sleep(1.0)
            elapsed += 1.0

        # Final segment: remaining watch time
        remaining = watch_time - elapsed
        if remaining > 3:
            time.sleep(remaining)

        _press_home(phone_id)
        log.info("[%s] YouTube warm-up done (watched ~%ds).", acc_id, int(watch_time))
        return True
    except Exception as e:
        log.warning("[%s] YouTube warm-up failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


def _warmup_gmail(phone_id: str, acc_id: str, log_acc_id: str = "") -> bool:
    """Open Gmail, scroll inbox, open the first email, read it, then return."""
    log.info("[%s] Gmail warm-up …", acc_id)
    if log_acc_id:
        try:
            from core.activity_log import log_event
            log_event(log_acc_id, "mobile", "gmail", {"action": "scroll inbox, open email"})
        except Exception:
            pass
    try:
        _open_app(phone_id, "com.google.android.gm", acc_id)

        # Scroll down through inbox to simulate reading
        _swipe_down(phone_id)
        time.sleep(random.uniform(2.0, 3.5))
        _swipe_down(phone_id)
        time.sleep(random.uniform(2.0, 3.5))

        # Tap the first visible email to open it
        tapped = _find_and_tap(phone_id, ["Inbox", "Primary", "Promotions", "Updates", "Social"])
        # The above tries to tap a tab which won't open an email — try tapping by position instead
        # Emails are typically at y~400-600 range in the list
        _shell(phone_id, "input tap 540 420")
        time.sleep(4)

        # Scroll through the email body
        _swipe_down(phone_id)
        time.sleep(random.uniform(3.0, 6.0))
        _swipe_down(phone_id)
        time.sleep(random.uniform(2.0, 4.0))

        # Go back to inbox
        _press_back(phone_id)
        time.sleep(2)

        _press_home(phone_id)
        log.info("[%s] Gmail warm-up done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Gmail warm-up failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


def _warmup_google_search(phone_id: str, acc_id: str, log_acc_id: str = "") -> bool:
    """Open Chrome → google.co.uk → search a query → scroll results → tap a result."""
    log.info("[%s] Google Search warm-up …", acc_id)
    try:
        # Open Chrome (wait until actually in foreground before proceeding)
        _shell(phone_id,
               "am start -a android.intent.action.VIEW "
               "-d 'https://www.google.co.uk' com.android.chrome")
        _wait_for_foreground_app(phone_id, "chrome", timeout=15, acc_id=acc_id)

        # Handle Chrome first-run welcome screen ("Welcome to Chrome" → Accept & continue)
        if _find_and_tap(phone_id, ["Accept & continue"]):
            log.info("[%s] Accepted Chrome terms of service.", acc_id)
            time.sleep(3)
            # Chrome may then offer to sign in — skip it
            _find_and_tap(phone_id, ["No thanks", "Skip", "Not now"])
            time.sleep(2)

        # Tap the search bar / address bar
        _find_and_tap(phone_id, ["Search or type URL", "Search Google or type a URL",
                                  "google.co.uk", "Address bar"])
        time.sleep(2)

        # Type search query
        query = random.choice(_GOOGLE_SEARCHES)
        log.info("[%s] Google search: %s", acc_id, query)
        if log_acc_id:
            try:
                from core.activity_log import log_event
                log_event(log_acc_id, "mobile", "google_search", {"search_term": query})
            except Exception:
                pass
        _type_and_search(phone_id, query)
        time.sleep(4)

        # Scroll results page
        _swipe_down(phone_id)
        time.sleep(random.uniform(2.0, 3.5))
        _swipe_down(phone_id)
        time.sleep(random.uniform(2.0, 3.5))

        # Tap a result — try to find organic result links by position
        # Results typically start around y=500; tap into that region
        _shell(phone_id, "input tap 540 600")
        time.sleep(5)

        # Scroll the page briefly
        _swipe_down(phone_id)
        time.sleep(random.uniform(4.0, 8.0))

        # Go back to results
        _press_back(phone_id)
        time.sleep(3)

        _press_home(phone_id)
        log.info("[%s] Google Search warm-up done.", acc_id)
        return True
    except Exception as e:
        log.warning("[%s] Google Search warm-up failed: %s", acc_id, e)
        _press_home(phone_id)
        return False


# ── Combined activities ──────────────────────────────────────────────────────

def _warmup_maps_then_youtube(phone_id: str, acc_id: str, account: dict,
                               log_acc_id: str = "") -> bool:
    """
    Run a Maps activity then a YouTube activity back-to-back.
    Randomly chooses between Maps browse and Maps directions for the first part.
    Returns True if at least one activity succeeded.
    """
    log.info("[%s] Maps+YouTube combined warm-up …", acc_id)
    maps_ok = False
    yt_ok = False

    # Randomly choose Maps browse or directions (60/40 split)
    if random.random() < 0.60:
        maps_ok = _warmup_maps(phone_id, acc_id, account, log_acc_id)
    else:
        maps_ok = _warmup_maps_directions(phone_id, acc_id, account, log_acc_id)

    # Always run YouTube after Maps (short gap between apps)
    time.sleep(random.uniform(2.0, 5.0))
    yt_ok = _warmup_youtube(phone_id, acc_id, account, log_acc_id)

    ok = maps_ok or yt_ok
    log.info("[%s] Maps+YouTube done (maps=%s, youtube=%s → overall=%s)",
             acc_id, maps_ok, yt_ok, ok)
    return ok


# ── Public activity selector ─────────────────────────────────────────────────

def run_selected_warmup(phone_id: str, acc_id: str, account: dict,
                        activity: str | None = None,
                        log_acc_id: str = "") -> tuple[bool, str, list]:
    """
    Run exactly one warm-up activity (or a combined routine).

    Args:
        phone_id:   GeelarK phone ID.
        acc_id:     Account ID (gl_xxx).
        account:    Account dict with geo/home/work data.
        activity:   One of "maps_browse", "maps_directions", "youtube",
                    "maps+youtube", "gmail", "google_search",
                    or None (None = weighted random choice from
                    maps_browse, maps_directions, youtube, maps+youtube).
        log_acc_id: Desktop account ID for activity logging (acc_xxx).

    Returns:
        (success: bool, activity_name: str, steps_done: list)
    """
    _ACTIVITY_MAP = {
        "maps_browse":     ("maps_browse",     _warmup_maps),
        "maps_directions": ("maps_directions", _warmup_maps_directions),
        "youtube":         ("youtube",         _warmup_youtube),
        "maps+youtube":    ("maps+youtube",    _warmup_maps_then_youtube),
        "gmail":           ("gmail",           _warmup_gmail),
        "google_search":   ("google_search",   _warmup_google_search),
    }

    # If no activity specified, randomly choose with weighted probabilities
    if not activity:
        choice = random.choices(
            ["maps_browse", "maps_directions", "youtube", "maps+youtube"],
            weights=[0.30, 0.25, 0.35, 0.10],
            k=1,
        )[0]
        activity = choice

    if activity not in _ACTIVITY_MAP:
        log.warning("[%s] Unknown activity %r — falling back to maps_browse", acc_id, activity)
        activity = "maps_browse"

    label, func = _ACTIVITY_MAP[activity]

    # Dispatch — gmail/google_search don't take account param
    if activity in ("gmail", "google_search"):
        ok = func(phone_id, acc_id, log_acc_id)
    else:
        ok = func(phone_id, acc_id, account, log_acc_id)

    steps = [label] if ok else []
    if ok:
        log.info("[%s] %s succeeded.", acc_id, label)
    else:
        log.warning("[%s] %s failed.", acc_id, label)

    return ok, label, steps


def run_warmup_activity(phone_id: str, account: dict,
                        activity: str | None = None,
                        log_acc_id: str = "") -> dict:
    """
    Thin wrapper around run_selected_warmup for the daily batch runner.

    Returns a dict: {"success": bool, "activity": str, "steps_done": [...]}
    """
    acc_id = account.get("id", "unknown")
    ok, label, steps = run_selected_warmup(
        phone_id, acc_id, account, activity=activity, log_acc_id=log_acc_id,
    )
    return {
        "success": ok,
        "activity": label,
        "steps_done": steps,
    }


# ── Session logger ────────────────────────────────────────────────────────────

def _log_session(log_file: Path, acc_id: str, steps: list, duration_s: float,
                 ip: str) -> None:
    """Append a session record to mobile_sessions.json."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    records: list = []
    if log_file.exists():
        try:
            records = json.loads(log_file.read_text(encoding="utf-8"))
        except Exception:
            records = []
    records.append({
        "account_id":  acc_id,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "ip":          ip,
        "steps_done":  steps,
        "duration_s":  round(duration_s, 1),
    })
    log_file.write_text(json.dumps(records, indent=2), encoding="utf-8")


# ── Schedule-driven session runner ────────────────────────────────────────────
# Replaces the old 5-activity random warmup with a single batch-mode script
# determined by the account's schedule_day using the monthly pattern.

def run_mobile_schedule_session(account: dict, log_file: Path,
                                progress_callback=None,
                                schedule_state: dict | None = None,
                                activity: str | None = None) -> dict:
    """
    Run ONE warm-up session for one GeelarK account.

    If ``activity`` is provided, runs exactly that activity.
    Otherwise the script is determined by the account's schedule_day count
    using _day_to_script() (the legacy batch-mode system).

    Keeps all the existing session infrastructure — proxy rotation, GPS,
    health check, phone start/stop, screenshot, session logging.

    Args:
        account:   dict from geelark_accounts.yaml
        log_file:  Path to mobile_sessions.json
        schedule_state:  MobileScheduler's raw state dict (or None if
                         running standalone / manual warmup)
        activity:  Optional activity name ("maps_browse", "maps_directions",
                   "youtube", "maps+youtube", "gmail", "google_search").
                   If None, uses the legacy schedule-driven batch selection.

    Returns:
        dict with keys: success, script, steps_done, duration_s, ip, error
    """
    from core.geelark_client import GeelarKClient

    acc_id   = account.get("id", "unknown")
    phone_id = account.get("geelark_phone_id")
    result   = {"success": False, "steps_done": [], "duration_s": 0.0, "ip": "", "error": ""}

    if not phone_id:
        result["error"] = "No phone_id — provision the phone first"
        return result

    if not account.get("mobile_warming_enabled", True):
        log.info("[%s] mobile_warming_enabled=false — skipping", acc_id)
        result["error"] = "warming disabled"
        return result

    t_start = time.time()
    client  = GeelarKClient()

    # Resolve desktop account ID (acc_xxx) by matching email in accounts.yaml
    # Mobile sessions are keyed gl_xxx; activity log uses acc_xxx for dashboard display
    log_acc_id = acc_id  # fallback to gl_id
    email = account.get("email", "").lower()
    if email:
        try:
            import yaml
            from core.paths import ACCOUNTS_FILE
            if ACCOUNTS_FILE.exists():
                data = yaml.safe_load(ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
                for a in data.get("accounts", []):
                    if a.get("email", "").lower() == email:
                        log_acc_id = a["id"]
                        break
        except Exception:
            pass

    # 0. Pre-flight health check — skip broken or occupied phones
    health = client.check_phone_health(phone_id)
    if not health["healthy"]:
        result["error"] = f"Phone health check failed: {health['reason']}"
        log.warning("[%s] Skipping warmup — phone unhealthy: %s", acc_id, health["reason"])
        return result

    # 0b. If phone is already running or occupied, skip safely (user may be
    # manually using another Geelark phone for social-media account creation)
    try:
        statuses = client.get_phone_status([phone_id])
        st = statuses[0].get("status") if statuses else -1
        if st in (0, 1):  # 0=Running, 1=Starting
            reason = f"Phone already in use (status={st}) — skipping to avoid conflict"
            result["error"] = reason
            log.warning("[%s] %s", acc_id, reason)
            return result
    except Exception as e:
        # If we cannot query status, continue but log it
        log.debug("[%s] Could not query phone status before start: %s", acc_id, e)

    # 1. Rotate proxy IP (cooldown is handled inside _rotate_proxy_ip)
    log.info("[%s] Rotating proxy IP …", acc_id)
    ip = _rotate_proxy_ip()
    result["ip"] = ip
    if ip:
        settle = random.uniform(5, 15)
        log.info("[%s] Waiting %.0fs (IP settling before phone start) …", acc_id, settle)
        time.sleep(settle)
    else:
        log.warning("[%s] Proxy rotation failed — proceeding with current IP", acc_id)

    # 2. Start phone
    log.info("[%s] Starting phone …", acc_id)
    try:
        viewer_url = client.start_phone(phone_id) or ""
        result["viewer_url"] = viewer_url
        if progress_callback:
            progress_callback({"phase": "running", "viewer_url": viewer_url})
    except Exception as e:
        result["error"] = f"Failed to start phone: {e}"
        return result

    # 3. Wait for phone to fully boot (replaces hardcoded sleep)
    log.info("[%s] Waiting for phone to boot …", acc_id)
    if not _wait_for_phone_ready(phone_id, acc_id, timeout=90):
        log.warning("[%s] Phone did not boot in 90s — proceeding anyway", acc_id)

    # 4. Refresh GPS (only if running legacy batch scripts; the new activity
    #    functions call ensure_location_and_permissions themselves)
    if activity is None:
        try:
            _refresh_gps(phone_id, account, acc_id)
            result["steps_done"].append("gps_refresh")
        except Exception as e:
            log.warning("[%s] GPS refresh failed: %s", acc_id, e)

    # 5. Run warm-up
    ok = False
    if activity:
        # ── Explicit activity mode ─────────────────────────────────────────
        ok, label, steps = run_selected_warmup(
            phone_id, acc_id, account, activity=activity, log_acc_id=log_acc_id,
        )
        result["steps_done"].extend(steps)
        result["script"] = label
        if ok and label not in result["steps_done"]:
            result["steps_done"].append(label)
    else:
        # ── Legacy schedule-driven batch mode ──────────────────────────────
        schedule_day = 1
        if schedule_state and acc_id in schedule_state:
            schedule_day = schedule_state[acc_id].get("schedule_day", 1)
        script = _day_to_script(schedule_day)
        log.info("[%s] Schedule day %d → script: %s", acc_id, schedule_day, script)

        if script == "brand_1km":
            ok = _batch_brand_1km(phone_id, acc_id, account, log_acc_id)
        elif script == "money_kw":
            ok = _batch_money_kw(phone_id, acc_id, account, log_acc_id)
        else:
            ok = _batch_local_discovery(phone_id, acc_id, account, log_acc_id)

        if ok:
            result["steps_done"].append(script)
            result["script"] = script
        else:
            result["script"] = script
            log.warning("[%s] %s returned False", acc_id, script)

    # 6. Proof screenshot — capture current screen before stopping
    try:
        from core.paths import LOGS_DIR
        from datetime import datetime as dt_module
        ts = dt_module.utcnow().strftime("%Y%m%d_%H%M%S")
        shot_name = f"{log_acc_id}_mobile_{ts}.png"
        shot_path = LOGS_DIR / "screenshots" / shot_name
        shot_path.parent.mkdir(parents=True, exist_ok=True)
        img_bytes = client.take_screenshot(phone_id)
        if img_bytes:
            shot_path.write_bytes(img_bytes)
            result["screenshot"] = f"screenshots/{shot_name}"
            from core.activity_log import log_event
            log_event(
                account_id    = log_acc_id,
                device        = "mobile",
                activity_type = "warmup_session",
                detail        = {"steps_done": result["steps_done"]},
                screenshot    = f"screenshots/{shot_name}",
            )
            log.info("[%s] Proof screenshot saved: %s", acc_id, shot_name)
    except Exception as e:
        log.debug("[%s] Screenshot failed: %s", acc_id, e)

    # 7. Stop phone — retry up to 3× and verify it actually stopped
    stopped = False
    for attempt in range(1, 4):
        try:
            client.stop_phone(phone_id)
        except Exception as e:
            log.warning("[%s] stop_phone attempt %d failed: %s", acc_id, attempt, e)
        # Poll status to confirm (GeelarK sometimes returns OK but keeps phone running)
        time.sleep(5)
        try:
            statuses = client.get_phone_status([phone_id])
            st = statuses[0].get("status") if statuses else -1
            if st in (2, 3):  # 2=Stopped, 3=Stopping/Expired
                log.info("[%s] Phone stopped (attempt %d, status=%d).", acc_id, attempt, st)
                stopped = True
                break
            log.warning("[%s] Phone still running after stop attempt %d (status=%d) — retrying",
                        acc_id, attempt, st)
        except Exception as e:
            log.warning("[%s] Could not verify phone status after stop: %s", acc_id, e)
            break
    if not stopped:
        log.error("[%s] Phone %s could NOT be confirmed stopped after 3 attempts — manual check needed",
                  acc_id, phone_id)

    result["duration_s"] = round(time.time() - t_start, 1)
    result["success"]    = len(result["steps_done"]) >= 1

    # 8. Log session
    try:
        _log_session(log_file, acc_id, result["steps_done"], result["duration_s"], ip)
    except Exception as e:
        log.warning("[%s] Session log write failed: %s", acc_id, e)

    log.info("[%s] Session complete. Steps: %s  Duration: %.0fs  IP: %s",
             acc_id, result["steps_done"], result["duration_s"], ip)
    return result


# Backward-compatible alias for existing callers (e.g. mobile_warmup_run.py)
run_warmup_session = run_mobile_schedule_session


def run_all_warmup_sessions(accounts: list, log_file: Path, progress_callback=None,
                            activity: str | None = None) -> list:
    """
    Run warm-up sessions for all enabled accounts, strictly sequentially.
    Never starts two phones at the same time — all share one mobile proxy.
    Skips accounts that already have a completed session today.
    """
    results = []
    enabled = [a for a in accounts
               if a.get("geelark_phone_id") and a.get("mobile_warming_enabled", True)]

    if not enabled:
        log.info("No accounts with mobile_warming_enabled=true and a phone_id.")
        return results

    # Skip phones already warmed today — safe to re-run "Start All" mid-day
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    done_today: set = set()
    if log_file.exists():
        try:
            records = json.loads(log_file.read_text(encoding="utf-8"))
            for r in records:
                if r.get("timestamp", "").startswith(today_str):
                    done_today.add(r.get("account_id", ""))
        except Exception:
            pass

    to_run  = [a for a in enabled if a["id"] not in done_today]
    skipped = [a for a in enabled if a["id"] in done_today]

    if skipped:
        log.info("Skipping %d account(s) already warmed today: %s",
                 len(skipped), [a["id"] for a in skipped])
        for acc in skipped:
            results.append({"account_id": acc["id"], "success": True,
                            "skipped": True, "reason": "already_done_today",
                            "steps_done": [], "duration_s": 0.0})

    if not to_run:
        log.info("All accounts already warmed today — nothing to do.")
        return results

    log.info("Starting mobile warm-up for %d account(s) (%d skipped, already done today): %s",
             len(to_run), len(skipped), [a["id"] for a in to_run])

    for i, acc in enumerate(to_run):
        if progress_callback:
            progress_callback({
                "phase":           f"phone_{i + 1}_of_{len(to_run)}",
                "current_account": acc["id"],
                "progress":        f"{i + 1}/{len(to_run)}",
            })
        r = run_mobile_schedule_session(acc, log_file,
                                         progress_callback=progress_callback,
                                         activity=activity)
        results.append({"account_id": acc["id"], **r})
        time.sleep(5)

    ok     = sum(1 for r in results if r.get("success") and not r.get("skipped"))
    failed = sum(1 for r in results if not r.get("success") and not r.get("skipped"))
    log.info("Mobile warm-up complete. OK: %d  Failed/partial: %d  Skipped (done today): %d",
             ok, failed, len(skipped))
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY — Old warmup activity functions.  Not called by the new schedule system.
# Kept for reference and as building blocks for the batch functions above.
# ═══════════════════════════════════════════════════════════════════════════════
