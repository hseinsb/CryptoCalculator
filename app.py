from flask import Flask, request, render_template, jsonify, redirect, url_for, session
from functools import wraps
import os
import requests
import openai
from dotenv import load_dotenv
import logging

load_dotenv()  # Load environment variables from a .env file

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Secret key for session management

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# API configuration
API_BASE_URL = "https://solana-gateway.moralis.io/token/mainnet"
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBSITE_PASSWORD = os.getenv("PASSWORD")

HEADERS = {
    "Accept": "application/json",
    "X-API-Key": MORALIS_API_KEY
}

# Initialize OpenAI client
client = openai.OpenAI(api_key=OPENAI_API_KEY)

def format_value(value):
    """Format numbers with appropriate decimal places for small values"""
    try:
        if value is None or value == "":
            return "N/A"
        num = float(value)
        if num < 0.01:
            return f"${num:.8f}".rstrip('0').rstrip('.')  # Show up to 8 decimal places for small values
        return f"${num:,.4f}".rstrip('0').rstrip('.')  # Show 4 decimal places for larger values
    except (ValueError, TypeError):
        return "N/A"

def format_supply(value):
    """Format supply numbers with commas"""
    try:
        if value is None or value == "":
            return "N/A"
        return f"{float(value):,.0f}"
    except (ValueError, TypeError):
        return "N/A"

def safe_float(value, default=0.0):
    """Safely convert value to float"""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (ValueError, TypeError):
        return default

def get_ratio_status(ratio_name, value):
    """Determine if a ratio is healthy based on predefined ranges"""
    if not isinstance(value, (int, float)):
        return 'red', '❌'
    
    conditions = {
        "Liquidity to Market Cap": lambda x: 5 <= x <= 30,
        "Buy/Sell Ratio": lambda x: 1 <= x <= 1.5,
        "Buys-to-Buyers": lambda x: 1 <= x <= 10,
        "Sells-to-Sellers": lambda x: 1 <= x <= 10,
        "Market Cap per Participant": lambda x: 500 <= x <= 5000,
        "Market Cap vs FDV": lambda x: x == 1.0,
        "Liquidity Pool vs Price": lambda x: x >= 10,
        "Volume vs Liquidity": lambda x: x <= 5
    }
    
    is_healthy = conditions.get(ratio_name, lambda x: False)(value)
    return ('green', '✅') if is_healthy else ('red', '❌')

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('authenticated'):
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        password = request.form.get('password')
        if password == WEBSITE_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Invalid password")
    return render_template('login.html')

@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login"))

@app.route("/fetch", methods=["POST"])
@login_required
def fetch():
    try:
        pair_address = request.form.get("tokenAddress")
        if not pair_address:
            logging.error("Token address is missing in the request.")
            return jsonify({"error": "Token address is missing"}), 400

        logging.debug(f"Received token address: {pair_address}")

        # First API call - Get pair stats
        pair_stats_url = f"{API_BASE_URL}/pairs/{pair_address}/stats"
        pair_response = requests.get(pair_stats_url, headers=HEADERS)
        pair_response.raise_for_status()
        stats = pair_response.json()
        logging.debug(f"Pair stats response: {stats}")

        # Extract token address from pair stats
        token_address = stats.get("tokenAddress")

        # Second API call - Get token metadata
        if token_address:
            metadata_url = f"{API_BASE_URL}/{token_address}/metadata"
            metadata_response = requests.get(metadata_url, headers=HEADERS)
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
        else:
            metadata = {}
        
        logging.debug(f"Token metadata response: {metadata}")

        # Get FDV value which we'll use for market cap
        fdv = safe_float(metadata.get("fullyDilutedValue"))

        # Calculate makers and transactions
        makers_24h = (
            safe_float(stats.get("buyers", {}).get("24h", 0)) +
            safe_float(stats.get("sellers", {}).get("24h", 0))
        )
        
        transactions_24h = (
            safe_float(stats.get("buys", {}).get("24h", 0)) +
            safe_float(stats.get("sells", {}).get("24h", 0))
        )

        metrics = {
            "name": stats.get("tokenName", "N/A"),
            "symbol": stats.get("tokenSymbol", "N/A"),
            "price": format_value(stats.get("currentUsdPrice")),
            "market_cap": format_value(fdv),  # Using FDV as market cap
            "fdv": format_value(fdv),
            "total_supply": format_supply(metadata.get("totalSupplyFormatted")),
            "liquidity": format_value(stats.get("totalLiquidityUsd")),
            "pair_label": stats.get("pairLabel", "N/A"),
            "exchange": stats.get("exchange", "N/A"),
            "price_change_24h": f"{stats.get('pricePercentChange', {}).get('24h', 'N/A')}%",
            "buy_volume_24h": format_value(stats.get("buyVolume", {}).get("24h")),
            "sell_volume_24h": format_value(stats.get("sellVolume", {}).get("24h")),
            "total_volume_24h": format_value(stats.get("totalVolume", {}).get("24h")),
            "buyers_24h": stats.get("buyers", {}).get("24h", "N/A"),
            "sellers_24h": stats.get("sellers", {}).get("24h", "N/A"),
            "buys_24h": stats.get("buys", {}).get("24h", "N/A"),
            "sells_24h": stats.get("sells", {}).get("24h", "N/A"),
            "makers_24h": int(makers_24h) if makers_24h != 0 else "N/A",
            "transactions_24h": int(transactions_24h) if transactions_24h != 0 else "N/A"
        }

        # Calculate ratios using FDV as market cap
        ratios = {
            "Liquidity to Market Cap": (
                (safe_float(stats.get("totalLiquidityUsd")) / fdv) * 100
                if stats.get("totalLiquidityUsd") and fdv else "N/A"
            ),
            "Buy/Sell Ratio": (
                safe_float(stats.get("buyVolume", {}).get("24h")) /
                safe_float(stats.get("sellVolume", {}).get("24h"))
                if stats.get("buyVolume", {}).get("24h") and
                stats.get("sellVolume", {}).get("24h") else "N/A"
            ),
            "Buys-to-Buyers": (
                safe_float(stats.get("buys", {}).get("24h")) /
                safe_float(stats.get("buyers", {}).get("24h"))
                if stats.get("buys", {}).get("24h") and
                stats.get("buyers", {}).get("24h") else "N/A"
            ),
            "Sells-to-Sellers": (
                safe_float(stats.get("sells", {}).get("24h")) /
                safe_float(stats.get("sellers", {}).get("24h"))
                if stats.get("sells", {}).get("24h") and
                stats.get("sellers", {}).get("24h") else "N/A"
            ),
            "Market Cap per Participant": (
                fdv /
                (safe_float(stats.get("buyers", {}).get("24h")) +
                safe_float(stats.get("sellers", {}).get("24h")))
                if fdv and stats.get("buyers", {}).get("24h") and
                stats.get("sellers", {}).get("24h") else "N/A"
            ),
            "Market Cap vs FDV": 1.0,  # Since we're using FDV as market cap, this is always 1
            "Liquidity Pool vs Price": (
                (safe_float(stats.get("totalLiquidityUsd")) /
                safe_float(stats.get("currentUsdPrice"))) * 100
                if stats.get("totalLiquidityUsd") and stats.get("currentUsdPrice") else "N/A"
            ),
            "Volume vs Liquidity": (
                safe_float(stats.get("totalVolume", {}).get("24h")) /
                safe_float(stats.get("totalLiquidityUsd"))
                if stats.get("totalVolume", {}).get("24h") and
                stats.get("totalLiquidityUsd") else "N/A"
            )
        }

        # Build metrics HTML with dividers
        metrics_html = f"""
        <div class="metric-grid">
            <div class="metric-card">
                <h3>📈 Basic Metrics</h3>
                <div class="metric-note">Note: Market Cap is derived from Fully Diluted Value (FDV)</div>
                {''.join([f'''
                <div class="metric-item">
                    <span>{key.replace("_", " ").title()}:</span>
                    <span>{value}</span>
                    <div class="metric-divider"></div>
                </div>
                ''' for key, value in metrics.items()])}
            </div>
            <div class="metric-card">
                <h3>🔍 Key Ratios</h3>
                {''.join([f'''
                <div class="metric-item">
                    <span>{ratio_name}:</span>
                    <span>
                        {round(value, 2) if isinstance(value, (int, float)) else "N/A"}
                        <span class="flag {get_ratio_status(ratio_name, value)[0]}">
                            {get_ratio_status(ratio_name, value)[1]}
                        </span>
                    </span>
                    <div class="metric-divider"></div>
                </div>
                ''' for ratio_name, value in ratios.items()])}
            </div>
        </div>
        """

        # Generate AI analysis with score and FDV note
        ai_prompt = """
        Analyze these crypto metrics and provide:
        1. Key observations about the token's performance
        2. Potential risks and red flags
        3. Opportunities and positive indicators
        4. A score from 1-100 (1 being very risky, 100 being very safe)

        Important Note: The Market Cap values shown are derived from the Fully Diluted Value (FDV)
        as direct market cap data is not available. This represents a theoretical maximum market cap
        assuming all tokens are in circulation.

        Use emojis for better readability.

        Scoring Guidelines:
        - Start with a base score of 50.
        - Add 10-20 points for positive metrics (e.g., healthy ratios, strong volume).
        - Subtract 5-15 points for negative metrics (e.g., poor ratios, low liquidity).
        End your analysis with 'Overall Score: X/100' on a new line.
        """

        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{
                "role": "system",
                "content": ai_prompt
            }, {
                "role": "user",
                "content": f"Token: {metrics['name']}\nMetrics: {metrics}\nRatios: {ratios}"
            }]
        )

        # Format the AI analysis with better spacing
        ai_analysis = (response.choices[0].message.content
            .replace("\n", "<br>")
            .replace("1.", "<br><br>1.")
            .replace("2.", "<br><br>2.")
            .replace("3.", "<br><br>3.")
            .replace("4.", "<br><br>4.")
            .replace("5.", "<br><br>5.")
            .replace("Overall Score:", "<br><br>Overall Score:"))

        full_response = f"""
        {metrics_html}
        <div class="metric-card">
            <h3>🤖 AI Analysis</h3>
            <div class="ai-response">{ai_analysis}</div>
        </div>
        """

        return jsonify({"response": full_response})

    except requests.exceptions.RequestException as e:
        logging.error(f"API Request Error: {str(e)}")
        return jsonify({"error": f"API Request Error: {str(e)}"}), 400
    except Exception as e:
        logging.error(f"Processing Error: {str(e)}")
        return jsonify({"error": f"Processing Error: {str(e)}"}), 400

if __name__ == "__main__":
    app.run(debug=True)