"""Probe public Lever/Ashby/Workable board APIs for notable product companies.

Reads curated GitHub slug lists, filters to MNCs / notable product companies
(India hiring preferred), skips tokens already in companies.yaml, and writes
YAML only for boards that return a valid public JSON payload.
"""

from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "research_ats_boards.yaml"
AGENT = Path(r"C:\Users\omkar\.cursor\projects\c-Projects\agent-tools")

LEVER_SLUGS_PATH = AGENT / "29f364df-1d58-4da2-9e67-ab0735e0da07.txt"
ASHBY_SLUGS_PATH = AGENT / "fed4e41e-3f89-48de-9e1b-63fffa077b61.txt"
HIRE_SIGNAL_PATH = AGENT / "00b6bbab-20f7-4ffc-a260-53d2c40df18b.txt"
WORKABLE_CAND_PATH = AGENT / "c4917a19-e048-4278-98e2-593dbd4c318e.txt"

SKIP_IDS = {
    "cred",
    "groww",
    "growwapp",
    "zepto",
    "meesho",
    "urbancompany",
    "urbanclap",
    "sarvam",
    "krutrim",
    "yellowai",
    "observeai",
    "slice",
    "sliceit",
    "fi",
    "epifi",
    "smallcase",
    "sharechat",
    "notion",
    "devrev",
    "elevation-capital",
    "elevation-capital-3",
    "huggingface",
}
SKIP_TOKENS = set(SKIP_IDS)

UA = (
    "Mozilla/5.0 (compatible; jobdigest-research/1.0; "
    "+https://github.com/jobdigest)"
)
INDIA_RE = re.compile(
    r"india|bengaluru|bangalore|hyderabad|mumbai|pune|chennai|noida|"
    r"gurgaon|gurugram|delhi|kolkata|ahmedabad|kochi|trivandrum|"
    r"thiruvananthapuram|jaipur|indore|nagpur|remote[ -]?india|"
    r"karnataka|telangana|maharashtra|tamil nadu|haryana",
    re.I,
)

# slug -> (display name, notes). India / MNC called out when known.
LEVER_NAMES: dict[str, tuple[str, str]] = {
    "100ms": ("100ms", "India product (Bengaluru); live jobs.lever.co/100ms"),
    "15five": ("15Five", "HR performance SaaS"),
    "1910genetics": ("1910 Genetics", "AI drug discovery"),
    "1inch": ("1inch", "DeFi aggregator"),
    "1password": ("1Password", "Password manager; may have migrated ATS"),
    "360learning": ("360Learning", "Collaborative learning SaaS"),
    "3box": ("3Box / Ceramic", "Web3 identity / Ceramic"),
    "3dhubs": ("3D Hubs (Fictiv)", "Manufacturing marketplace"),
    "42dot": ("42dot", "Hyundai autonomous driving"),
    "7geese": ("7Geese", "OKR / performance SaaS"),
    "8x8": ("8x8", "UCaaS public company"),
    "90seconds": ("90 Seconds", "Video production platform"),
    "99taxis": ("99", "Latin America ride-hailing (Didi)"),
    "ablspacesystems": ("ABL Space Systems", "Launch / aerospace"),
    "abridge": ("Abridge", "Clinical AI documentation"),
    "abtasty": ("AB Tasty", "Experimentation / CRO SaaS"),
    "academia": ("Academia.edu", "Research network"),
    "acala": ("Acala", "Polkadot DeFi"),
    "acast": ("Acast", "Podcast platform"),
    "acceldata": ("Acceldata", "Data observability; India engineering"),
    "achievers": ("Achievers", "Employee recognition SaaS"),
    "actian": ("Actian", "Data platform"),
    "activecampaign": ("ActiveCampaign", "Marketing automation"),
    "ada": ("Ada", "Customer AI / chatbots"),
    "adcolony": ("AdColony", "Mobile ads (Digital Turbine)"),
    "adthena": ("Adthena", "Competitive intelligence SaaS"),
    "aeva": ("Aeva", "LiDAR / perception"),
    "affinity": ("Affinity", "Relationship intelligence CRM"),
    "affirm": ("Affirm", "BNPL public fintech"),
    "afterpaytouch": ("Afterpay", "BNPL (Block)"),
    "agicap": ("Agicap", "Cash management SaaS"),
    "agiloft": ("Agiloft", "CLM SaaS"),
    "aidash": ("AiDash", "Satellite AI for utilities"),
    "airalo": ("Airalo", "eSIM marketplace"),
    "aircall": ("Aircall", "Cloud phone system"),
    "airdna": ("AirDNA", "Short-term rental data"),
    "airship": ("Airship", "Mobile engagement"),
    "airslate": ("airSlate", "Document workflow"),
    "airtm": ("Airtm", "Digital dollar platform"),
    "airwallex": ("Airwallex", "Global payments; APAC hiring"),
    "akashnetwork": ("Akash Network", "Decentralized cloud"),
    "alan": ("Alan", "French digital health insurer"),
    "alation": ("Alation", "Data catalog"),
    "alchemy": ("Alchemy", "Web3 infra"),
    "algolia": ("Algolia", "Search API"),
    "alloy": ("Alloy", "Identity / fraud decisioning"),
    "alltrails": ("AllTrails", "Outdoor consumer app"),
    "alluxio": ("Alluxio", "Data orchestration"),
    "amplitude": ("Amplitude", "Product analytics public co"),
    "amyris": ("Amyris", "Synthetic biology"),
    "anduril": ("Anduril", "Defense tech"),
    "anthropic": ("Anthropic", "Frontier AI; may have moved ATS"),
    "anyscale": ("Anyscale", "Ray / AI infra"),
    "applovin": ("AppLovin", "Mobile ads / gaming public co"),
    "atlassian": ("Atlassian", "MNC developer tools; India offices"),
    "auth0": ("Auth0", "Identity (Okta)"),
    "benchling": ("Benchling", "Life-science R&D cloud"),
    "binance": ("Binance", "Crypto exchange"),
    "bitpanda": ("Bitpanda", "European crypto broker"),
    "blablacar": ("BlaBlaCar", "European mobility marketplace"),
    "bolt": ("Bolt", "European mobility / delivery"),
    "box": ("Box", "Content cloud public co"),
    "buildkite": ("Buildkite", "CI/CD"),
    "canva": ("Canva", "Design platform; India hiring historically"),
    "carta": ("Carta", "Equity management"),
    "checkout": ("Checkout.com", "Global payments"),
    "clevertap": ("CleverTap", "India product (Mumbai) engagement SaaS"),
    "coda": ("Coda", "Doc / all-in-one workplace"),
    "cohere": ("Cohere", "Enterprise LLM"),
    "coinmarketcap": ("CoinMarketCap", "Crypto data (Binance)"),
    "coursera": ("Coursera", "Edtech public co"),
    "cruise": ("Cruise", "Autonomous vehicles (GM)"),
    "crypto": ("Crypto.com", "Crypto exchange"),
    "curefit": ("Cult / Curefit", "India consumer health / fitness"),
    "databricks": ("Databricks", "Data/AI platform; India GDC"),
    "dgraph": ("Dgraph", "Graph DB; India-founded (Hasura-adjacent team history)"),
    "duolingo": ("Duolingo", "Language learning public co"),
    "etsy": ("Etsy", "Marketplace public co"),
    "eventbrite": ("Eventbrite", "Events platform public co"),
    "fampay": ("Fam / FamPay", "India teen/UPI fintech"),
    "figma": ("Figma", "Design; may have moved ATS"),
    "gitlab": ("GitLab", "DevSecOps public co"),
    "gojek": ("Gojek", "SEA super-app"),
    "grab": ("Grab", "SEA super-app public co"),
    "highspot": ("Highspot", "Sales enablement"),
    "honeycomb": ("Honeycomb", "Observability"),
    "hotstar": ("JioHotstar / Hotstar", "India streaming (legacy Lever board)"),
    "icertis": ("Icertis", "India-founded CLM SaaS MNC"),
    "imbue": ("Imbue", "AI research lab"),
    "immuta": ("Immuta", "Data security"),
    "immutable": ("Immutable", "Web3 gaming"),
    "imperfectfoods": ("Imperfect Foods", "Consumer grocery"),
    "impossiblefoods": ("Impossible Foods", "Alt-protein"),
    "includedhealth": ("Included Health", "Virtual care"),
    "incorta": ("Incorta", "Analytics platform"),
    "indigoag": ("Indigo Ag", "Agtech"),
    "inflection": ("Inflection AI", "Consumer AI"),
    "inkitt": ("Inkitt / Galatea", "Fiction / publishing tech"),
    "instructure": ("Instructure", "Canvas LMS"),
    "intenseye": ("Intenseye", "Workplace safety AI"),
    "ionic": ("Ionic", "Cross-platform app platform"),
    "ironcladapp": ("Ironclad", "CLM"),
    "issuu": ("Issuu", "Digital publishing"),
    "jagex": ("Jagex", "RuneScape / gaming"),
    "jamcity": ("Jam City", "Mobile gaming"),
    "jar-app": ("Jar", "India gold/savings fintech"),
    "jina-ai": ("Jina AI", "Neural search; Berlin/China, global"),
    "jiostar": ("JioStar", "India media (Disney+ Hotstar / Jio)"),
    "jupiter": ("Jupiter", "India neobank"),
    "klarna": ("Klarna", "BNPL unicorn"),
    "kong": ("Kong", "API gateway"),
    "kraken": ("Kraken", "Crypto exchange"),
    "launchdarkly": ("LaunchDarkly", "Feature flags"),
    "lever": ("Lever", "ATS vendor"),
    "lyrahealth": ("Lyra Health", "Mental health benefits"),
    "matchgroup": ("Match Group", "Tinder/Hinge public co"),
    "medium": ("Medium", "Publishing platform"),
    "metabase": ("Metabase", "Open-source BI"),
    "mindtickle": ("Mindtickle", "India-founded revenue enablement SaaS"),
    "minted": ("Minted", "Consumer marketplace"),
    "mistral": ("Mistral AI", "European frontier AI"),
    "netflix": ("Netflix", "MNC streaming; India titles historically"),
    "openai": ("OpenAI", "Frontier AI; likely migrated to Ashby"),
    "opensea": ("OpenSea", "NFT marketplace"),
    "outreach": ("Outreach", "Sales engagement"),
    "pagerduty": ("PagerDuty", "Incident response public co"),
    "palantir": ("Palantir", "MNC data platform; India hiring historically"),
    "paytm": ("Paytm", "India fintech public co"),
    "paytmpayments": ("Paytm Payments Services", "India payments (PG)"),
    "pipedrive": ("Pipedrive", "CRM SaaS"),
    "pivotal": ("Pivotal", "Tanzu / cloud native (legacy)"),
    "plaid": ("Plaid", "Open banking; likely migrated to Ashby"),
    "porter": ("Porter", "India logistics / intra-city freight"),
    "procreate": ("Savage / Procreate", "Creative software"),
    "prodigygame": ("Prodigy Education", "Edtech games"),
    "pspdfkit": ("PSPDFKit / Nutrient", "Document SDK"),
    "q-ctrl": ("Q-CTRL", "Quantum software"),
    "qonto": ("Qonto", "European business neobank"),
    "quantcast": ("Quantcast", "Adtech / measurement"),
    "quantummetric": ("Quantum Metric", "Digital analytics"),
    "quicknode": ("QuickNode", "Web3 infra"),
    "quince": ("Quince", "DTC apparel"),
    "quizizz": ("Quizizz", "India-founded edtech"),
    "quizlet-2": ("Quizlet", "Edtech study platform"),
    "quora": ("Quora", "Q&A consumer; likely migrated to Ashby"),
    "rackspace": ("Rackspace", "Cloud MNC"),
    "radai": ("Rad AI", "Radiology AI"),
    "rappi": ("Rappi", "LatAm super-app"),
    "rapidapi": ("RapidAPI", "API marketplace"),
    "rarible": ("Rarible", "NFT marketplace"),
    "reddit": ("Reddit", "Consumer social public co"),
    "retool": ("Retool", "Internal tools; likely migrated to Ashby"),
    "revolut": ("Revolut", "Global neobank"),
    "scaleai": ("Scale AI", "Data for AI"),
    "secureframe": ("Secureframe", "Compliance SaaS"),
    "shieldai": ("Shield AI", "Defense autonomy"),
    "shopify": ("Shopify", "Commerce MNC; India GDC historically"),
    "spotify": ("Spotify", "MNC audio; India market"),
    "swiggy": ("Swiggy", "India food/quick-commerce unicorn"),
    "sysdig": ("Sysdig", "Cloud security"),
    "tala": ("Tala", "Emerging-market fintech (India/KE/PH)"),
    "toptal": ("Toptal", "Talent network"),
    "twitch": ("Twitch", "Amazon streaming"),
    "udemy": ("Udemy", "Edtech public co"),
    "verkada": ("Verkada", "Physical security"),
    "waveapps": ("Wave", "SMB accounting"),
    "whoop": ("WHOOP", "Wearables"),
    "wish": ("Wish / ContextLogic", "E-commerce"),
    "xero": ("Xero", "Accounting SaaS public co"),
    "yelp": ("Yelp", "Local marketplace public co"),
    "zendesk": ("Zendesk", "CX SaaS"),
    # Extra India / APAC probes (may 404 if not on Lever)
    "razorpay": ("Razorpay", "India payments unicorn"),
    "browserstack": ("BrowserStack", "India-founded browser testing"),
    "chargebee": ("Chargebee", "India-founded billing SaaS"),
    "hasura": ("Hasura", "India-founded GraphQL"),
    "whatfix": ("Whatfix", "India DAP / digital adoption"),
    "innovaccer": ("Innovaccer", "India-founded health data"),
    "druva": ("Druva", "India-founded data protection"),
    "acko": ("Acko", "India insurer"),
    "navi": ("Navi", "India fintech (Sachin Bansal)"),
    "unacademy": ("Unacademy", "India edtech"),
    "scaler": ("Scaler / InterviewBit", "India edtech"),
    "cashfree": ("Cashfree", "India payments"),
    "juspay": ("Juspay", "India payments / HyperCheckout"),
    "shiprocket": ("Shiprocket", "India e-commerce logistics"),
    "oyo": ("OYO", "India hospitality"),
    "makemytrip": ("MakeMyTrip", "India travel public co"),
    "ixigo": ("ixigo", "India travel"),
    "ola": ("Ola", "India mobility"),
    "blinkit": ("Blinkit", "India quick commerce (Zomato)"),
    "zomato": ("Zomato", "India food public co"),
    "phonepe": ("PhonePe", "India UPI / Walmart"),
    "nykaa": ("Nykaa", "India beauty public co"),
    "lenskart": ("Lenskart", "India eyewear"),
    "delhivery": ("Delhivery", "India logistics public co"),
    "policybazaar": ("Policybazaar", "India insurtech public co"),
    "dream11": ("Dream11", "India fantasy sports"),
    "healthifyme": ("HealthifyMe", "India health app"),
    "khatabook": ("Khatabook", "India SMB ledger"),
    "niyo": ("Niyo", "India neobank"),
    "instamojo": ("Instamojo", "India payments / SMB"),
    "postman": ("Postman", "India-founded API platform"),
    "freshworks": ("Freshworks", "India-founded CX public co"),
    "dunzo": ("Dunzo", "India hyperlocal (legacy)"),
    "cars24": ("Cars24", "India auto marketplace"),
    "meesho": ("Meesho", "SKIP if listed — India social commerce"),
}

ASHBY_NAMES: dict[str, tuple[str, str]] = {
    "1password": ("1Password", "Password manager"),
    "andela": ("Andela", "Global engineering talent"),
    "anyscale": ("Anyscale", "Ray / AI infra"),
    "ashby": ("Ashby", "ATS vendor"),
    "baseten": ("Baseten", "ML inference"),
    "benchling": ("Benchling", "Life-science R&D"),
    "braintrust": ("Braintrust", "Talent network"),
    "character": ("Character.AI", "Consumer AI"),
    "clerk": ("Clerk", "Auth / user management"),
    "clickup": ("ClickUp", "Work OS"),
    "cohere": ("Cohere", "Enterprise LLM"),
    "confluent": ("Confluent", "Kafka public co; India GDC"),
    "crusoe": ("Crusoe", "AI compute / energy"),
    "cursor": ("Cursor / Anysphere", "AI code editor"),
    "decagon": ("Decagon", "AI customer support"),
    "deel": ("Deel", "Global payroll; India EOR hiring"),
    "deepgram": ("Deepgram", "Speech AI"),
    "docker": ("Docker", "Containers"),
    "drata": ("Drata", "Compliance automation"),
    "elevenlabs": ("ElevenLabs", "Voice AI"),
    "figma": ("Figma", "Design (Adobe)"),
    "goteleport": ("Teleport", "Infrastructure access"),
    "hackerone": ("HackerOne", "Security / bug bounty"),
    "handshake": ("Handshake", "Campus recruiting"),
    "harvey": ("Harvey", "Legal AI"),
    "helion": ("Helion Energy", "Fusion"),
    "hightouch": ("Hightouch", "Reverse ETL"),
    "hopper": ("Hopper", "Travel super-app"),
    "illumio": ("Illumio", "Zero-trust segmentation"),
    "jasper": ("Jasper", "Marketing AI"),
    "juicebox": ("Juicebox / PeopleGPT", "Recruiting AI"),
    "kong": ("Kong", "API gateway"),
    "kustomer": ("Kustomer", "CX platform (Meta)"),
    "langchain": ("LangChain", "LLM framework"),
    "linear": ("Linear", "Issue tracker"),
    "livekit": ("LiveKit", "Realtime media"),
    "m-kopa": ("M-KOPA", "Africa/India-adjacent fintech energy"),
    "mercor": ("Mercor", "AI talent marketplace"),
    "motion": ("Motion", "AI calendar / work"),
    "navi": ("Navi", "India fintech"),
    "neon": ("Neon", "Serverless Postgres"),
    "notion": ("Notion", "SKIP — already in companies.yaml"),
    "openai": ("OpenAI", "Frontier AI; India-eligible roles appear"),
    "oso": ("Oso", "Authorization"),
    "paragon": ("Paragon", "Embedded iPaaS"),
    "patreon": ("Patreon", "Creator economy"),
    "perplexity": ("Perplexity", "AI search"),
    "persona": ("Persona", "Identity verification"),
    "pika": ("Pika", "Video AI"),
    "pinecone": ("Pinecone", "Vector DB"),
    "plaid": ("Plaid", "Open banking"),
    "posthog": ("PostHog", "Product analytics OSS"),
    "primer": ("Primer", "Payments orchestration"),
    "quora": ("Quora", "Q&A"),
    "railway": ("Railway", "App hosting"),
    "ramp": ("Ramp", "Spend management"),
    "reka": ("Reka AI", "Multimodal models"),
    "render": ("Render", "Cloud hosting"),
    "replit": ("Replit", "Cloud IDE"),
    "retool": ("Retool", "Internal tools"),
    "runway": ("Runway", "Generative video"),
    "semgrep": ("Semgrep", "Code security"),
    "sentry": ("Sentry", "Error monitoring; India GDC historically"),
    "sierra": ("Sierra", "AI customer agents"),
    "sieve": ("Sieve", "Video AI infra"),
    "skydio": ("Skydio", "Autonomous drones"),
    "snowflake": ("Snowflake", "Data cloud MNC; India GDC"),
    "snyk": ("Snyk", "Developer security"),
    "sprig": ("Sprig", "Product research"),
    "stedi": ("Stedi", "EDI / B2B APIs"),
    "suno": ("Suno", "Music AI"),
    "supabase": ("Supabase", "Open-source Firebase"),
    "svix": ("Svix", "Webhooks"),
    "synthesia": ("Synthesia", "AI video avatars"),
    "tldraw": ("tldraw", "Canvas SDK"),
    "vanta": ("Vanta", "Trust / compliance"),
    "vercel": ("Vercel", "Frontend cloud"),
    "whatnot": ("Whatnot", "Live shopping"),
    "workos": ("WorkOS", "Enterprise auth"),
    "writer": ("Writer", "Enterprise generative AI"),
    "zapier": ("Zapier", "Automation"),
    "fullstory": ("Fullstory", "Digital experience"),
    "golinks": ("GoLinks", "Link shortener / knowledge"),
    "greptile": ("Greptile", "AI code review"),
    "freshpaint": ("Freshpaint", "HIPAA analytics"),
    "creditgenie": ("Credit Genie", "Consumer finance"),
    "chainalysis-careers": ("Chainalysis", "Crypto compliance"),
    "cointracker": ("CoinTracker", "Crypto tax"),
    "phantom": ("Phantom", "Solana wallet"),
    "uniswap": ("Uniswap Labs", "DeFi protocol"),
    "backpack": ("Backpack", "Crypto wallet / exchange"),
    "trust-wallet": ("Trust Wallet", "Web3 wallet (Binance)"),
    "rain": ("Rain", "Crypto cards"),
    "kraken.com": ("Kraken", "Crypto exchange"),
    "mystenlabs": ("Mysten Labs", "Sui blockchain"),
    "stytch": ("Stytch", "Auth APIs"),
    "spoton": ("SpotOn", "SMB payments"),
    "nerdwallet": ("NerdWallet", "Consumer finance public co"),
    "oneapp": ("OnePay", "Consumer finance"),
    "dave": ("Dave", "Neobank public co"),
    "junipersquare": ("Juniper Square", "Real-estate GP software"),
    "meridianlink": ("MeridianLink", "Lending software public co"),
    "finalis": ("Finalis", "RIA / broker-dealer ops"),
    "wealth-com": ("Wealth.com", "Estate planning"),
    "polymarket": ("Polymarket", "Prediction markets"),
    "sleeper": ("Sleeper", "Sports / fantasy"),
    "cedar": ("Cedar", "Healthcare billing"),
    "envoy": ("Envoy", "Workplace platform"),
    "g2": ("G2", "Software marketplace"),
    "givebutter": ("Givebutter", "Nonprofit fundraising"),
    "anrok": ("Anrok", "SaaS tax"),
    "orb": ("Orb", "Usage billing"),
    "orum": ("Orum", "Instant payments"),
    "jellyfish": ("Jellyfish", "Engineering management"),
    "siro": ("Siro", "Field sales AI"),
    "strava": ("Strava", "Fitness social"),
    "vetcove": ("Vetcove", "Vet supply marketplace"),
    "capsule": ("Capsule", "Pharmacy"),
    "chownow": ("ChowNow", "Restaurant online ordering"),
    "mosey": ("Mosey", "Employment compliance"),
    "revenuecat": ("RevenueCat", "In-app subscriptions"),
    "redis": ("Redis", "Data platform public co"),
    "sysdig": ("Sysdig", "Cloud security"),
    "aclima": ("Aclima", "Climate sensing"),
    "cape": ("Cape", "Privacy hardware"),
    "higharc": ("Higharc", "Homebuilding software"),
    "pano-ai": ("Pano AI", "Wildfire detection"),
    "solace": ("Solace", "Event-driven integration"),
    "onebrief": ("Onebrief", "Defense planning software"),
    "permitflow": ("PermitFlow", "Construction permitting"),
    "twelve-labs": ("Twelve Labs", "Video understanding AI"),
    "hebbia-ai": ("Hebbia", "AI for knowledge work"),
    "cartesia": ("Cartesia", "Voice / sonic AI"),
    "retell-ai": ("Retell AI", "Voice agents"),
    "credal": ("Credal", "Secure AI for enterprises"),
    "eliseai": ("EliseAI", "Property AI"),
    "nooks": ("Nooks", "AI sales dialer"),
    "mirage": ("Mirage", "AI creative"),
    "fleetworks": ("FleetWorks", "Freight AI"),
    "vizcom": ("Vizcom", "AI design"),
    "siftstack": ("Sift", "Industrial AI"),
    "ditto": ("Ditto", "Edge sync / local-first"),
    "poshmark": ("Poshmark", "Fashion marketplace (NAVER)"),
    "partiful": ("Partiful", "Events consumer"),
    "workwhilejobs": ("WorkWhile", "Hourly labor"),
    "bubble": ("Bubble", "No-code"),
    "mural": ("Mural", "Visual collaboration"),
    "frontcareers": ("Front", "Customer comms inbox"),
    "read-ai": ("Read AI", "Meeting intelligence"),
}

WORKABLE_NOTABLE = {
    "typeform": ("Typeform", "Forms / surveys SaaS"),
    "hotjar": ("Hotjar", "Product analytics (Contentsquare)"),
    "mux": ("Mux", "Video infra API"),
    "loom": ("Loom", "Async video (Atlassian)"),
    "persona": ("Persona", "Identity verification"),
    "deel": ("Deel", "Global payroll — verify if still Workable vs Ashby"),
    "sephora": ("Sephora", "LVMH beauty MNC"),
    "forbes": ("Forbes", "Media"),
    "samsung-sds-america": ("Samsung SDS America", "Samsung IT services MNC"),
    "fergus": ("Fergus", "Trade job-management SaaS"),
    "lingoace": ("LingoAce", "Edtech; APAC/India-adjacent hiring"),
    "gamigo": ("Gamigo", "Games publisher"),
    "healthsnap": ("HealthSnap", "Remote patient monitoring"),
    "akur8": ("Akur8", "Insurtech pricing SaaS"),
    "iglooinsure": ("Igloo", "SEA insurtech; India ops"),
    "groundtruth": ("GroundTruth", "Location ads; India roles"),
    "reveal-health-tech": ("Reveal HealthTech", "US+India health tech"),
    "apna": ("Apna", "India jobs / professional network unicorn"),
    "weekday-1": ("Weekday", "India YC recruiting / talent"),
    "astrome-technologies": ("Astrome Technologies", "India mmWave / satellite deep tech"),
    "blue-machines-ai": ("Blue Machines AI", "India enterprise voice AI"),
    "virallens": ("Virallens", "India AI product studio"),
    "seekho": ("Seekho", "India consumer video"),
    "maxwellenergy": ("Maxwell Energy Systems", "India BMS / EV deep tech"),
    "rapyuta-robotics": ("Rapyuta Robotics", "Cloud robotics; Chennai hub"),
    "tiger-analytics": ("Tiger Analytics", "India-founded AI consultancy"),
    "zifo": ("Zifo", "India scientific informatics"),
    "hike": ("Hike", "India gaming / Rush"),
    "indusface": ("Indusface", "India WAAP / appsec SaaS"),
    "darwinbox": ("Darwinbox", "India HCM SaaS unicorn"),
    "vahan": ("Vahan", "India blue-collar hiring AI"),
    "toppr": ("Toppr", "India edtech"),
    "tryzensglobal": ("Tryzens Global", "Commerce consultancy; Kerala delivery"),
    "infuse-it": ("Infuse Consulting", "QE/DevOps; Goa office"),
    "eastencher": ("Eastencher Software", "Bengaluru product engineering"),
    "hyperverge": ("HyperVerge", "India identity / KYC AI"),
    "2070health": ("2070 Health", "India health venture studio"),
    "aion": ("AION", "Enterprise AI; India hybrid roles"),
    "zenara-health": ("Zenara Health", "India remote healthtech"),
    "walletconnect": ("WalletConnect", "Web3 wallet protocol"),
    "genie-ai": ("Genie AI", "Legal AI"),
    "smartnews": ("SmartNews", "News app"),
    "kobalt-music": ("Kobalt Music", "Music rights"),
    "semios": ("Semios", "Agtech"),
    "channable": ("Channable", "Marketplace feed SaaS"),
    "constructor-1": ("Constructor", "Search / discovery"),
    "deep-science-ventures": ("Deep Science Ventures", "Deep-tech venture studio"),
    "mention-me-ltd": ("Mention Me", "Referral marketing SaaS"),
    "pearltalent": ("Pearl Talent", "Global talent"),
    "gbg": ("GBG", "Identity data public co"),
    "inkomoko": ("Inkomoko", "East Africa SME finance"),
    "kwara": ("Kwara", "African core banking SaaS"),
    "weekday": ("Weekday", "Alt slug probe"),
}


def title_name(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").replace(".", " ").title()


def india_hits(blob: str) -> bool:
    return bool(blob and INDIA_RE.search(blob))


def lever_locations(jobs: list) -> str:
    locs = []
    for j in jobs[:80]:
        cat = j.get("categories") or {}
        locs.append(str(cat.get("location") or j.get("country") or ""))
    return " | ".join(locs)


def ashby_locations(jobs: list) -> str:
    locs = []
    for j in jobs[:80]:
        locs.append(str(j.get("location") or ""))
        for loc in j.get("locations") or []:
            if isinstance(loc, dict):
                locs.append(str(loc.get("locationName") or loc.get("name") or loc))
            else:
                locs.append(str(loc))
    return " | ".join(locs)


def workable_locations(jobs: list) -> str:
    locs = []
    for j in jobs[:80]:
        loc = j.get("location") or {}
        if isinstance(loc, dict):
            locs.append(
                " ".join(
                    str(x)
                    for x in (
                        loc.get("city"),
                        loc.get("region"),
                        loc.get("country"),
                        loc.get("country_code"),
                    )
                    if x
                )
            )
        else:
            locs.append(str(loc))
        locs.append(str(j.get("shortcode") or ""))
    return " | ".join(locs)


def probe_lever(client: httpx.Client, token: str) -> dict | None:
    urls = [
        f"https://api.lever.co/v0/postings/{token}?mode=json",
        f"https://api.eu.lever.co/v0/postings/{token}?mode=json",
    ]
    last_err = None
    for url in urls:
        try:
            r = client.get(url, timeout=20.0)
        except httpx.HTTPError as exc:
            last_err = str(exc)
            continue
        if r.status_code == 404:
            last_err = "404"
            continue
        if r.status_code != 200:
            last_err = f"HTTP {r.status_code}"
            continue
        try:
            data = r.json()
        except Exception:
            return None
        if not isinstance(data, list):
            return None
        loc_blob = lever_locations(data)
        region = "eu" if "eu.lever" in url else "us"
        return {
            "ok": True,
            "jobs": len(data),
            "india": india_hits(loc_blob),
            "sample": (data[0].get("text") if data else ""),
            "api": url,
            "region": region,
            "err": last_err,
        }
    return None


def probe_ashby(client: httpx.Client, token: str) -> dict | None:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    try:
        r = client.get(url, timeout=20.0)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    jobs = data.get("jobs") or data.get("jobPostings") or []
    if not isinstance(jobs, list):
        return None
    loc_blob = ashby_locations(jobs)
    return {
        "ok": True,
        "jobs": len(jobs),
        "india": india_hits(loc_blob),
        "sample": (jobs[0].get("title") if jobs else ""),
        "api": url,
    }


def probe_workable(client: httpx.Client, token: str) -> dict | None:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
    try:
        r = client.get(url, timeout=25.0)
    except httpx.HTTPError:
        return None
    if r.status_code in {403, 429}:
        time.sleep(8)
        try:
            r = client.get(url, timeout=25.0)
        except httpx.HTTPError:
            return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return None
    loc_blob = workable_locations(jobs) + " " + str(data.get("name") or "")
    return {
        "ok": True,
        "jobs": len(jobs),
        "india": india_hits(loc_blob),
        "sample": (jobs[0].get("title") if jobs else ""),
        "api": url,
        "board_name": data.get("name") or "",
    }


def skipped(id_: str, token: str) -> bool:
    return id_.lower() in SKIP_IDS or token.lower() in SKIP_TOKENS


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def confidence(jobs: int, notable: bool) -> str:
    if jobs > 0 and notable:
        return "high"
    if jobs > 0:
        return "high"
    if notable:
        return "medium"
    return "low"


def main() -> int:
    lever_slugs = set(load_json(LEVER_SLUGS_PATH))
    ashby_slugs = set(load_json(ASHBY_SLUGS_PATH))
    hire = load_json(HIRE_SIGNAL_PATH)
    wk_cands = load_json(WORKABLE_CAND_PATH)

    lever_candidates: dict[str, tuple[str, str]] = {}
    for slug, meta in LEVER_NAMES.items():
        lever_candidates[slug] = meta
    ashby_candidates: dict[str, tuple[str, str]] = dict(ASHBY_NAMES)

    for row in hire:
        src = (row.get("source") or "").lower()
        token = str(row.get("id") or "").strip()
        name = str(row.get("name") or title_name(token))
        if not token:
            continue
        notes = f"hire-signal {row.get('sector') or 'tech'} company"
        if src == "lever":
            lever_candidates.setdefault(token, (name, notes))
        elif src == "ashby":
            ashby_candidates.setdefault(token, (name, notes))

    # Prefer Feashliaa-listed slugs but still probe India extras not in the dump.
    lever_probe = []
    for token, (name, notes) in lever_candidates.items():
        if skipped(token, token):
            continue
        in_list = token in lever_slugs
        lever_probe.append((token, name, notes, in_list))

    ashby_probe = []
    for token, (name, notes) in ashby_candidates.items():
        if skipped(token, token):
            continue
        in_list = token in ashby_slugs
        ashby_probe.append((token, name, notes, in_list))

    wk_probe = []
    seen_wk = set()
    for row in wk_cands:
        token = str(row.get("companySlug") or "").strip()
        name = str(row.get("displayName") or title_name(token))
        hq = str(row.get("hq") or "")
        sector = str(row.get("sector") or "")
        desc = str(row.get("description") or "")
        if not token or skipped(token, token):
            continue
        india = india_hits(hq + " " + desc)
        notable = token in WORKABLE_NOTABLE
        productish = bool(
            re.search(
                r"SaaS|fintech|AI|product|gaming|media|cyber|HR tech|edtech|"
                r"insurtech|deep tech|marketplace|payments|developer|crypto",
                sector,
                re.I,
            )
        )
        if not (india or notable or productish):
            continue
        extra = WORKABLE_NOTABLE.get(token, (name, sector))
        notes = extra[1]
        if india:
            notes = (notes + "; India HQ/ops").strip("; ")
        wk_probe.append((token, extra[0] if notable else name, notes, True))
        seen_wk.add(token)
    for token, (name, notes) in WORKABLE_NOTABLE.items():
        if token in seen_wk or skipped(token, token):
            continue
        wk_probe.append((token, name, notes, False))

    headers = {"User-Agent": UA, "Accept": "application/json"}
    results: list[dict] = []

    print(f"Probing Lever {len(lever_probe)} | Ashby {len(ashby_probe)} | Workable {len(wk_probe)}", flush=True)

    with httpx.Client(headers=headers, follow_redirects=True, http2=False) as client:
        def run_lever(item):
            token, name, notes, in_list = item
            hit = probe_lever(client, token)
            return ("lever", token, name, notes, in_list, hit)

        def run_ashby(item):
            token, name, notes, in_list = item
            hit = probe_ashby(client, token)
            return ("ashby", token, name, notes, in_list, hit)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futs = [pool.submit(run_lever, it) for it in lever_probe]
            futs += [pool.submit(run_ashby, it) for it in ashby_probe]
            done = 0
            total = len(futs)
            for fut in as_completed(futs):
                done += 1
                if done % 25 == 0:
                    print(f"  lever/ashby {done}/{total}", flush=True)
                results.append(fut.result())

        wk_results = []
        for i, item in enumerate(wk_probe, 1):
            token, name, notes, in_list = item
            hit = probe_workable(client, token)
            wk_results.append(("workable", token, name, notes, in_list, hit))
            if i % 15 == 0:
                print(f"  workable {i}/{len(wk_probe)}", flush=True)
            time.sleep(0.35)
        results.extend(wk_results)

    companies = []
    counts = {"lever": 0, "ashby": 0, "workable": 0}
    for ats, token, name, notes, in_list, hit in sorted(results, key=lambda x: (x[0], x[2].lower())):
        if not hit:
            continue
        cid = re.sub(r"[^a-z0-9-]+", "-", token.lower()).strip("-")
        if skipped(cid, token):
            continue
        jobs = hit["jobs"]
        india = hit.get("india")
        sources = []
        if in_list:
            sources.append("Feashliaa/job-board-aggregator slug list" if ats != "workable" else "ever-jobs workable-candidates")
        if ats == "ashby" and token in ASHBY_NAMES:
            sources.append("GitHub hire-signal and/or public jobs.ashbyhq.com listing")
        if ats == "lever":
            sources.append(f"GET {hit.get('api')} → 200 JSON array ({jobs} jobs)")
        elif ats == "ashby":
            sources.append(f"GET {hit.get('api')} → 200 JSON jobs ({jobs} jobs)")
        else:
            sources.append(f"GET {hit.get('api')} → 200 widget JSON ({jobs} jobs)")
        note_parts = [notes] if notes else []
        if india:
            note_parts.append("India locations present on live board")
        if jobs == 0:
            note_parts.append("valid empty board at probe time")
        if hit.get("region") == "eu":
            note_parts.append("EU Lever host api.eu.lever.co")
        if hit.get("sample"):
            note_parts.append(f"sample role: {hit['sample'][:80]}")
        note_parts.append("evidence: " + "; ".join(sources))
        rec = {
            "id": cid,
            "name": name,
            "ats": ats,
            "token": token,
            "notes": "; ".join(p for p in note_parts if p),
            "confidence": confidence(jobs, True),
        }
        companies.append(rec)
        counts[ats] += 1

    payload = {
        "generated": "public ATS board research (Lever / Ashby / Workable)",
        "skip_already_in_companies_yaml": sorted(SKIP_IDS),
        "counts": counts,
        "companies": companies,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    print("Wrote", OUT)
    print("counts", counts, "total", len(companies))
    return 0


if __name__ == "__main__":
    sys.exit(main())
