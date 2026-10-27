"""Developer-tools example data. Loaded only when the topic table is empty.

Item tuple: (name, domains separated by spaces, needs_review, notes).
Domains are left empty unless they are well known. Everything except the big public
registries is marked needs_review so the admin verifies it before exporting an allowlist.
"""

from psycopg import sql

SEED = [
    {"name": "IDEs", "icon": "code", "items": [
        ("VS Code", "marketplace.visualstudio.com update.code.visualstudio.com open-vsx.org", True, "Updates and extension marketplace"),
        ("Cursor", "cursor.com", True, ""),
        ("Antigravity", "", True, ""),
        ("Windsurf", "", True, ""),
        ("Devin", "devin.ai", True, ""),
        ("JetBrains IDEs", "plugins.jetbrains.com download.jetbrains.com", True, "Plugins and updates"),
        ("Zed", "zed.dev", True, ""),
        ("Kiro", "", True, ""),
    ]},
    {"name": "Code extensions & agents", "icon": "terminal", "items": [
        ("Claude Code", "api.anthropic.com claude.ai", True, ""),
        ("ChatGPT / Codex", "api.openai.com chatgpt.com", True, ""),
        ("DeepSeek", "api.deepseek.com", True, ""),
        ("OpenCode", "opencode.ai", True, ""),
        ("Cline", "", True, ""),
        ("Continue", "", True, ""),
        ("GitHub Copilot", "api.githubcopilot.com", True, ""),
        ("Gemini CLI", "generativelanguage.googleapis.com", True, ""),
        ("Roo Code", "", True, ""),
        ("Aider", "", True, ""),
    ]},
    {"name": "Local LLM runtimes", "icon": "cpu", "items": [
        ("llama.cpp", "huggingface.co", True, "Model downloads"),
        ("Ollama", "ollama.com registry.ollama.ai", True, "Model pulls"),
        ("LM Studio", "lmstudio.ai huggingface.co", True, ""),
        ("vLLM", "huggingface.co", True, ""),
        ("Jan", "jan.ai", True, ""),
    ]},
    {"name": "Inference providers", "icon": "zap", "items": [
        ("Together AI", "api.together.xyz", True, ""),
        ("Groq", "api.groq.com", True, ""),
        ("Fireworks AI", "api.fireworks.ai", True, ""),
        ("OpenRouter", "openrouter.ai", True, ""),
        ("Replicate", "api.replicate.com", True, ""),
        ("Hugging Face Inference", "api-inference.huggingface.co", True, ""),
        ("Cerebras", "api.cerebras.ai", True, ""),
        ("DeepInfra", "api.deepinfra.com", True, ""),
    ]},
    {"name": "LLM providers", "icon": "layers", "items": [
        ("Anthropic", "api.anthropic.com", True, ""),
        ("OpenAI", "api.openai.com", True, ""),
        ("Google Gemini", "generativelanguage.googleapis.com", True, ""),
        ("DeepSeek", "api.deepseek.com", True, ""),
        ("Mistral", "api.mistral.ai", True, ""),
        ("Cohere", "api.cohere.com", True, ""),
    ]},
    {"name": "APIs", "icon": "globe", "children": [
        {"name": "Media generation", "icon": "film", "items": [
            ("Image processing demo", "images.example.test", True, "Synthetic API example"),
            ("Media queue demo", "media.example.test", True, "Synthetic API example"),
            ("fal.ai", "fal.run queue.fal.run", True, ""),
            ("Runway", "api.runwayml.com", True, ""),
            ("Luma", "", True, ""),
            ("Black Forest Labs", "", True, ""),
            ("Stability AI", "api.stability.ai", True, ""),
            ("Kling", "", True, ""),
        ]},
        {"name": "Speech & audio", "icon": "mic", "items": [
            ("ElevenLabs", "api.elevenlabs.io", True, ""),
            ("Deepgram", "api.deepgram.com", True, ""),
            ("AssemblyAI", "api.assemblyai.com", True, ""),
            ("Cartesia", "", True, ""),
            ("Google Cloud Speech", "speech.googleapis.com", True, ""),
        ]},
        {"name": "Weather", "icon": "sun", "items": [
            ("OpenWeatherMap", "api.openweathermap.org", True, ""),
            ("Open-Meteo", "api.open-meteo.com", True, ""),
            ("WeatherAPI", "api.weatherapi.com", True, ""),
            ("Tomorrow.io", "api.tomorrow.io", True, ""),
        ]},
        {"name": "Maps & geo", "icon": "map", "items": [
            ("Google Maps Platform", "maps.googleapis.com", True, ""),
            ("Mapbox", "api.mapbox.com", True, ""),
            ("OpenStreetMap / Nominatim", "nominatim.openstreetmap.org", True, ""),
            ("HERE", "", True, ""),
        ]},
        {"name": "Search & web data", "icon": "search", "items": [
            ("Tavily", "api.tavily.com", True, ""),
            ("Serper", "google.serper.dev", True, ""),
            ("Brave Search API", "api.search.brave.com", True, ""),
            ("Exa", "api.exa.ai", True, ""),
            ("Firecrawl", "api.firecrawl.dev", True, ""),
            ("SerpAPI", "serpapi.com", True, ""),
        ]},
        {"name": "Payments", "icon": "credit-card", "items": [
            ("Stripe", "api.stripe.com", True, ""),
            ("PayPal", "api.paypal.com", True, ""),
            ("Payment sandbox demo", "payments.example.test", True, "Synthetic API example"),
        ]},
        {"name": "Messaging", "icon": "message-circle", "items": [
            ("Twilio", "api.twilio.com", True, ""),
            ("WhatsApp Business (Meta)", "graph.facebook.com", True, ""),
            ("Telegram Bot API", "api.telegram.org", True, ""),
            ("SendGrid", "api.sendgrid.com", True, ""),
            ("Resend", "api.resend.com", True, ""),
        ]},
        {"name": "Data & finance", "icon": "trending-up", "items": [
            ("Alpha Vantage", "www.alphavantage.co", True, ""),
            ("Polygon.io", "api.polygon.io", True, ""),
            ("Finnhub", "finnhub.io", True, ""),
            ("CoinGecko", "api.coingecko.com", True, ""),
            ("World Bank API", "api.worldbank.org", True, ""),
        ]},
        {"name": "Translation", "icon": "type", "items": [
            ("DeepL", "api.deepl.com", True, ""),
            ("Google Translate", "translation.googleapis.com", True, ""),
            ("Azure Translator", "api.cognitive.microsofttranslator.com", True, ""),
        ]},
        {"name": "Social & content", "icon": "share-2", "items": [
            ("Instagram Graph API", "graph.facebook.com", True, ""),
            ("YouTube Data API", "www.googleapis.com", True, ""),
            ("X API", "api.x.com", True, ""),
            ("LinkedIn API", "api.linkedin.com", True, ""),
        ]},
    ]},
    {"name": "Cloud & backend services", "icon": "cloud", "items": [
        ("Supabase", "supabase.com supabase.co", True, ""),
        ("Firebase", "firebase.google.com firebaseio.com", True, ""),
        ("Vercel", "vercel.com", True, ""),
        ("Cloudflare", "cloudflare.com api.cloudflare.com", True, ""),
        ("Netlify", "netlify.com", True, ""),
        ("AWS", "amazonaws.com", True, "Broad; admin may want to narrow"),
        ("Google Cloud", "googleapis.com cloud.google.com", True, "Broad; admin may want to narrow"),
        ("Azure", "azure.com windows.net", True, "Broad; admin may want to narrow"),
    ]},
    {"name": "Package registries", "icon": "package", "items": [
        ("PyPI (all packages)", "pypi.org files.pythonhosted.org", False, "Covers pandas, opencv, transformers, etc."),
        ("npm (all packages)", "registry.npmjs.org", False, "Covers npx"),
        ("System packages (apt/brew)", "archive.ubuntu.com security.ubuntu.com deb.debian.org formulae.brew.sh", True, "Covers ffmpeg and similar tools"),
        ("Docker Hub", "registry-1.docker.io auth.docker.io", False, ""),
        ("Conda", "conda.anaconda.org repo.anaconda.com", True, ""),
        ("crates.io", "crates.io static.crates.io", True, ""),
        ("Maven Central", "repo1.maven.org", True, ""),
        ("Go modules", "proxy.golang.org", True, ""),
    ]},
    {"name": "Source & hubs", "icon": "git-branch", "items": [
        ("GitHub", "github.com api.github.com raw.githubusercontent.com objects.githubusercontent.com", False, "git over HTTPS, releases, raw files"),
        ("GitLab", "gitlab.com", True, ""),
        ("Hugging Face Hub", "huggingface.co", False, "Models and datasets"),
        ("Kaggle", "kaggle.com", True, ""),
    ]},
]


def _add(conn, nodes, parent_id, dynamic=True):
    for i, node in enumerate(nodes):
        cid = conn.execute(
            sql.SQL("insert into ") + sql.Identifier("x_topic")
            + sql.SQL(" (parent_id, name, icon, sort) values (%s,%s,%s,%s) returning id"),
            (parent_id, node["name"], node.get("icon", "folder"), i),
        ).fetchone()["id"]
        for j, (name, domains, review, notes) in enumerate(node.get("items", [])):
            conn.execute(
                sql.SQL("insert into ") + sql.Identifier("x_tool")
                + sql.SQL(" (") + sql.Identifier("parent_id")
                + sql.SQL(", name, domains, needs_review, notes, sort) values (%s,%s,%s,%s,%s,%s)"),
                (cid, name, domains.split(), review, notes, j),
            )
        _add(conn, node.get("children", []), cid, dynamic)


def seed_catalog(conn, dynamic=True) -> None:
    if not dynamic:
        raise ValueError("Example seeds only support spec-generated tables")
    if conn.execute(sql.SQL("select 1 from ") + sql.Identifier("x_topic") + sql.SQL(" limit 1")).fetchone():
        return
    _add(conn, SEED, None, dynamic)
