import logging
import pandas as pd
import numpy as np
import requests
import MetaTrader5 as mt5
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext, MessageHandler, Filters
import talib
from datetime import datetime, timedelta
import schedule
import time
import threading
from tabulate import tabulate

# ===== CONFIGURATION =====
# Replace these values with your actual credentials
TOKEN = "8269947031:AAHI92w0sCTo0842N0FBBsJDIQkAp83IOko"
ADMIN_ID = "8367788232"  # Get this from @userinfobot on Telegram
TWELVE_DATA_API_KEY = "82a9a98cc0f7401a9140f7fe0bf4e4b8"
WALLET_ADDRESS = "0xF662cE8D1c415b480582f06b2f69eF31e639Db76"

# Bot settings
TRIAL_DAYS = 3
SUBSCRIPTION_PRICES = {
    '1month': 50,
    '3months': 120,
    '6months': 200
}

# Forex and Crypto pairs
FOREX_PAIRS = [
    'EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF', 'AUD/USD', 'USD/CAD', 'NZD/USD',
    'EUR/GBP', 'EUR/JPY', 'GBP/JPY', 'AUD/JPY', 'EUR/CAD', 'GBP/CHF', 'CAD/JPY'
]

CRYPTO_SYMBOLS = [
    'BTC/USD', 'ETH/USD', 'XRP/USD', 'LTC/USD', 'BCH/USD', 
    'ADA/USD', 'DOT/USD', 'LINK/USD', 'BNB/USD', 'XLM/USD'
]

# ===== INITIALIZATION =====
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("victorex_trader.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# In-memory storage (use database in production)
users = {}
trial_users = {}
subscribed_users = {}
banned_users = set()
mt5_accounts = {}  # user_id -> MT5 account details

# ===== MT5 INTEGRATION =====
def initialize_mt5():
    """Initialize MT5 connection"""
    if not mt5.initialize():
        logger.error("MT5 initialization failed")
        return False
    logger.info("MT5 initialized successfully")
    return True

def login_mt5(account, password, server):
    """Login to MT5 account"""
    authorized = mt5.login(account, password=password, server=server)
    if not authorized:
        logger.error(f"MT5 login failed for account {account}")
        return False
    logger.info(f"MT5 login successful for account {account}")
    return True

def get_mt5_account_info():
    """Get MT5 account information"""
    account_info = mt5.account_info()
    if account_info is None:
        logger.error("Failed to get MT5 account info")
        return None
    
    return {
        'balance': account_info.balance,
        'equity': account_info.equity,
        'margin': account_info.margin,
        'free_margin': account_info.margin_free,
        'profit': account_info.profit
    }

def get_mt5_positions():
    """Get MT5 positions"""
    positions = mt5.positions_get()
    if positions is None:
        logger.error("Failed to get MT5 positions")
        return []
    
    return [
        {
            'symbol': pos.symbol,
            'type': 'BUY' if pos.type == 0 else 'SELL',
            'volume': pos.volume,
            'profit': pos.profit,
            'price': pos.price_current
        }
        for pos in positions
    ]

def get_mt5_history(days=7):
    """Get MT5 history"""
    from_date = datetime.now() - timedelta(days=days)
    deals = mt5.history_deals_get(from_date, datetime.now())
    if deals is None:
        logger.error("Failed to get MT5 history")
        return []
    
    return [
        {
            'symbol': deal.symbol,
            'type': 'BUY' if deal.type == 0 else 'SELL',
            'volume': deal.volume,
            'profit': deal.profit,
            'time': datetime.fromtimestamp(deal.time).strftime("%Y-%m-%d %H:%M:%S")
        }
        for deal in deals
    ]

# Initialize MT5
initialize_mt5()

# ===== TECHNICAL ANALYSIS =====
def get_technical_indicators(data):
    """Calculate technical indicators from price data"""
    df = pd.DataFrame(data)
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['volume'] = df['volume'].astype(float)
    
    # Calculate indicators
    df['rsi'] = talib.RSI(df['close'], timeperiod=14)
    df['macd'], df['macd_signal'], df['macd_hist'] = talib.MACD(df['close'], fastperiod=12, slowperiod=26, signalperiod=9)
    df['sma20'] = talib.SMA(df['close'], timeperiod=20)
    df['sma50'] = talib.SMA(df['close'], timeperiod=50)
    df['bb_upper'], df['bb_middle'], df['bb_lower'] = talib.BBANDS(df['close'], timeperiod=20)
    df['stoch_k'], df['stoch_d'] = talib.STOCH(df['high'], df['low'], df['close'], fastk_period=14, slowk_period=3, slowd_period=3)
    
    return df.iloc[-1]

def generate_signal(symbol, interval='1min'):
    """Generate trading signal for a symbol"""
    try:
        # Fetch data from Twelve Data API
        url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&apikey={TWELVE_DATA_API_KEY}"
        response = requests.get(url)
        data = response.json()
        
        if 'values' not in data:
            return "No data available"
        
        # Process data and calculate indicators
        latest_indicators = get_technical_indicators(data['values'])
        
        # Generate signal based on indicators
        signal = "NEUTRAL"
        confidence = 0
        
        # RSI based signal
        if latest_indicators['rsi'] < 30:
            signal = "BUY"
            confidence += 0.3
        elif latest_indicators['rsi'] > 70:
            signal = "SELL"
            confidence += 0.3
        
        # MACD based signal
        if latest_indicators['macd'] > latest_indicators['macd_signal']:
            signal = "BUY"
            confidence += 0.2
        else:
            signal = "SELL"
            confidence += 0.2
        
        # Moving average crossover
        if latest_indicators['sma20'] > latest_indicators['sma50']:
            signal = "BUY"
            confidence += 0.2
        else:
            signal = "SELL"
            confidence += 0.2
        
        # Bollinger Bands
        if latest_indicators['close'] < latest_indicators['bb_lower']:
            signal = "BUY"
            confidence += 0.15
        elif latest_indicators['close'] > latest_indicators['bb_upper']:
            signal = "SELL"
            confidence += 0.15
        
        # Stochastic
        if latest_indicators['stoch_k'] < 20 and latest_indicators['stoch_d'] < 20:
            signal = "BUY"
            confidence += 0.15
        elif latest_indicators['stoch_k'] > 80 and latest_indicators['stoch_d'] > 80:
            signal = "SELL"
            confidence += 0.15
        
        confidence = min(confidence, 1.0)
        
        return {
            'symbol': symbol,
            'signal': signal,
            'confidence': confidence,
            'price': latest_indicators['close'],
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'indicators': {
                'rsi': round(latest_indicators['rsi'], 2),
                'macd': round(latest_indicators['macd'], 5),
                'macd_signal': round(latest_indicators['macd_signal'], 5),
                'sma20': round(latest_indicators['sma20'], 5),
                'sma50': round(latest_indicators['sma50'], 5)
            }
        }
    except Exception as e:
        logger.error(f"Error generating signal: {str(e)}")
        return f"Error generating signal: {str(e)}"

# ===== TELEGRAM BOT FUNCTIONS =====
def start(update: Update, context: CallbackContext):
    """Start command handler"""
    user_id = update.effective_user.id
    
    if user_id in banned_users:
        update.message.reply_text("⛔ Your account has been banned. Contact admin for support.")
        return
    
    if user_id not in users:
        users[user_id] = {
            'start_date': datetime.now(),
            'trial_end': datetime.now() + timedelta(days=TRIAL_DAYS),
            'subscribed': False,
            'subscription_end': None
        }
        trial_users[user_id] = users[user_id]
    
    keyboard = [
        [InlineKeyboardButton("📈 Generate Signal", callback_data='generate_signal')],
        [InlineKeyboardButton("📊 Account Info", callback_data='account_info')],
        [InlineKeyboardButton("🔗 Connect MT5", callback_data='connect_mt5')],
        [InlineKeyboardButton("💰 Subscribe", callback_data='subscribe')],
        [InlineKeyboardButton("🆘 Help", callback_data='help')]
    ]
    
    if user_id == int(ADMIN_ID):
        keyboard.append([InlineKeyboardButton("👨‍💼 Admin Panel", callback_data='admin_panel')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    update.message.reply_text(
        "🤖 Welcome to Victorex Trader AI Bot!\n\n"
        "I provide Forex and Crypto trading signals based on technical analysis.\n\n"
        f"Your trial period ends: {users[user_id]['trial_end'].strftime('%Y-%m-%d %H:%M:%S')}",
        reply_markup=reply_markup
    )

def button_handler(update: Update, context: CallbackContext):
    """Handle inline keyboard button presses"""
    query = update.callback_query
    query.answer()
    user_id = query.from_user.id
    
    if user_id in banned_users:
        query.edit_message_text("⛔ Your account has been banned. Contact admin for support.")
        return
    
    if query.data == 'generate_signal':
        generate_signal_menu(query)
    elif query.data == 'account_info':
        get_account_info(query)
    elif query.data == 'connect_mt5':
        connect_mt5_menu(query)
    elif query.data == 'subscribe':
        show_subscription_options(query)
    elif query.data == 'help':
        show_help(query)
    elif query.data == 'admin_panel' and user_id == int(ADMIN_ID):
        show_admin_panel(query)
    elif query.data.startswith('signal_'):
        asset_type, symbol = query.data.split('_')[1], query.data.split('_')[2]
        generate_specific_signal(query, symbol)
    elif query.data == 'forex_pairs':
        show_forex_pairs(query)
    elif query.data == 'crypto_pairs':
        show_crypto_pairs(query)
    elif query.data.startswith('subscribe_'):
        subscription_type = query.data.split('_')[1]
        process_subscription(query, subscription_type)
    elif query.data == 'back_to_main':
        start_from_query(query)
    elif query.data == 'mt5_info':
        show_mt5_info(query)
    elif query.data == 'mt5_positions':
        show_mt5_positions(query)
    elif query.data == 'mt5_history':
        show_mt5_history(query)
    elif query.data == 'mt5_back':
        connect_mt5_menu(query)

def generate_signal_menu(query):
    """Show signal generation menu"""
    keyboard = [
        [InlineKeyboardButton("💱 Forex Pairs", callback_data='forex_pairs')],
        [InlineKeyboardButton("₿ Crypto Pairs", callback_data='crypto_pairs')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_to_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text("Select asset type:", reply_markup=reply_markup)

def show_forex_pairs(query):
    """Show Forex pairs selection"""
    keyboard = []
    row = []
    for i, pair in enumerate(FOREX_PAIRS):
        row.append(InlineKeyboardButton(pair, callback_data=f'signal_forex_{pair}'))
        if (i + 1) % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data='generate_signal')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text("Select Forex pair:", reply_markup=reply_markup)

def show_crypto_pairs(query):
    """Show Crypto pairs selection"""
    keyboard = []
    row = []
    for i, symbol in enumerate(CRYPTO_SYMBOLS):
        row.append(InlineKeyboardButton(symbol, callback_data=f'signal_crypto_{symbol}'))
        if (i + 1) % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data='generate_signal')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text("Select Crypto pair:", reply_markup=reply_markup)

def generate_specific_signal(query, symbol):
    """Generate signal for a specific symbol"""
    user_id = query.from_user.id
    
    # Check if user has access
    if not has_access(user_id):
        query.edit_message_text(
            "❌ Your trial has expired. Please subscribe to continue using signals.\n\n"
            "Use /subscribe to see subscription options."
        )
        return
    
    query.edit_message_text(f"⏳ Generating signal for {symbol}...")
    
    # Clean symbol for API call
    clean_symbol = symbol.replace('/', '')
    signal = generate_signal(clean_symbol)
    
    if isinstance(signal, str):
        query.edit_message_text(signal)
        return
    
    message = (
        f"🎯 Signal for {signal['symbol']}\n"
        f"📊 Signal: {signal['signal']}\n"
        f"💪 Confidence: {signal['confidence']*100:.2f}%\n"
        f"💰 Price: {signal['price']}\n"
        f"⏰ Time: {signal['timestamp']}\n\n"
        f"Technical Indicators:\n"
        f"RSI: {signal['indicators']['rsi']}\n"
        f"MACD: {signal['indicators']['macd']}\n"
        f"MACD Signal: {signal['indicators']['macd_signal']}\n"
        f"SMA20: {signal['indicators']['sma20']}\n"
        f"SMA50: {signal['indicators']['sma50']}\n\n"
        f"⚠️ Disclaimer: This is not financial advice. Trade at your own risk."
    )
    
    query.edit_message_text(message)

def connect_mt5_menu(query):
    """Show MT5 connection menu"""
    user_id = query.from_user.id
    has_connected = user_id in mt5_accounts
    
    keyboard = []
    if has_connected:
        keyboard.extend([
            [InlineKeyboardButton("📊 Account Info", callback_data='mt5_info')],
            [InlineKeyboardButton("📈 Open Positions", callback_data='mt5_positions')],
            [InlineKeyboardButton("📋 Trade History", callback_data='mt5_history')]
        ])
    else:
        keyboard.append([InlineKeyboardButton("🔗 Connect MT5 Account", callback_data='connect_mt5_input')])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data='back_to_main')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    status_text = "✅ Connected" if has_connected else "❌ Not Connected"
    message = f"🔗 MT5 Account Connection\n\nStatus: {status_text}\n\nSelect an option:"
    
    query.edit_message_text(message, reply_markup=reply_markup)

def show_mt5_info(query):
    """Show MT5 account information"""
    user_id = query.from_user.id
    
    if user_id not in mt5_accounts:
        query.edit_message_text("❌ Please connect your MT5 account first.")
        return
    
    # Login to MT5
    account_details = mt5_accounts[user_id]
    if not login_mt5(account_details['account'], account_details['password'], account_details['server']):
        query.edit_message_text("❌ Failed to connect to MT5. Please check your credentials.")
        return
    
    # Get account info
    account_info = get_mt5_account_info()
    if account_info is None:
        query.edit_message_text("❌ Failed to retrieve account information.")
        return
    
    message = (
        f"📊 MT5 Account Information\n\n"
        f"💵 Balance: ${account_info['balance']:.2f}\n"
        f"📈 Equity: ${account_info['equity']:.2f}\n"
        f"📉 Margin: ${account_info['margin']:.2f}\n"
        f"🆓 Free Margin: ${account_info['free_margin']:.2f}\n"
        f"💰 Profit: ${account_info['profit']:.2f}"
    )
    
    keyboard = [
        [InlineKeyboardButton("⬅️ Back", callback_data='mt5_back')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_text(message, reply_markup=reply_markup)

def show_mt5_positions(query):
    """Show MT5 positions"""
    user_id = query.from_user.id
    
    if user_id not in mt5_accounts:
        query.edit_message_text("❌ Please connect your MT5 account first.")
        return
    
    # Login to MT5
    account_details = mt5_accounts[user_id]
    if not login_mt5(account_details['account'], account_details['password'], account_details['server']):
        query.edit_message_text("❌ Failed to connect to MT5. Please check your credentials.")
        return
    
    # Get positions
    positions = get_mt5_positions()
    
    if not positions:
        message = "No open positions found."
    else:
        message = "📈 Open Positions\n\n"
        for pos in positions:
            message += f"{pos['symbol']} - {pos['type']} - Volume: {pos['volume']} - Profit: ${pos['profit']:.2f}\n"
    
    keyboard = [
        [InlineKeyboardButton("⬅️ Back", callback_data='mt5_back')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_text(message, reply_markup=reply_markup)

def show_mt5_history(query):
    """Show MT5 history"""
    user_id = query.from_user.id
    
    if user_id not in mt5_accounts:
        query.edit_message_text("❌ Please connect your MT5 account first.")
        return
    
    # Login to MT5
    account_details = mt5_accounts[user_id]
    if not login_mt5(account_details['account'], account_details['password'], account_details['server']):
        query.edit_message_text("❌ Failed to connect to MT5. Please check your credentials.")
        return
    
    # Get history
    history = get_mt5_history(7)  # Last 7 days
    
    if not history:
        message = "No trade history found for the last 7 days."
    else:
        message = "📋 Trade History (Last 7 Days)\n\n"
        for trade in history:
            message += f"{trade['time']} - {trade['symbol']} - {trade['type']} - Profit: ${trade['profit']:.2f}\n"
    
    keyboard = [
        [InlineKeyboardButton("⬅️ Back", callback_data='mt5_back')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_text(message, reply_markup=reply_markup)

def handle_mt5_credentials(update: Update, context: CallbackContext):
    """Handle MT5 credentials input"""
    user_id = update.effective_user.id
    text = update.message.text
    
    try:
        # Parse credentials (format: account,password,server)
        account, password, server = text.split(',')
        account = int(account.strip())
        
        # Test connection
        if not login_mt5(account, password.strip(), server.strip()):
            update.message.reply_text("❌ Failed to connect to MT5. Please check your credentials and try again.")
            return
        
        # Store credentials
        mt5_accounts[user_id] = {
            'account': account,
            'password': password.strip(),
            'server': server.strip()
        }
        
        update.message.reply_text("✅ MT5 account connected successfully!")
        
        # Return to main menu
        start(update, context)
    except ValueError:
        update.message.reply_text("❌ Invalid format. Please send your credentials in the format: AccountNumber,Password,Server")
    except Exception as e:
        logger.error(f"Error connecting to MT5: {str(e)}")
        update.message.reply_text("❌ An error occurred. Please try again later.")

def get_account_info(query):
    """Get account information"""
    user_id = query.from_user.id
    
    if user_id not in mt5_accounts:
        query.edit_message_text("❌ Please connect your MT5 account first using the 'Connect MT5' option.")
        return
    
    # Login to MT5
    account_details = mt5_accounts[user_id]
    if not login_mt5(account_details['account'], account_details['password'], account_details['server']):
        query.edit_message_text("❌ Failed to connect to MT5. Please check your credentials.")
        return
    
    # Get account info
    account_info = get_mt5_account_info()
    if account_info is None:
        query.edit_message_text("❌ Failed to retrieve account information.")
        return
    
    # Get positions
    positions = get_mt5_positions()
    
    message = (
        f"📊 Account Information\n\n"
        f"💵 Balance: ${account_info['balance']:.2f}\n"
        f"📈 Equity: ${account_info['equity']:.2f}\n"
        f"📉 Margin: ${account_info['margin']:.2f}\n"
        f"🆓 Free Margin: ${account_info['free_margin']:.2f}\n"
        f"💰 Profit: ${account_info['profit']:.2f}\n\n"
        f"📋 Open Positions: {len(positions)}\n"
    )
    
    for pos in positions:
        message += f"{pos['symbol']} - {pos['type']} - Volume: {pos['volume']} - Profit: ${pos['profit']:.2f}\n"
    
    keyboard = [
        [InlineKeyboardButton("⬅️ Back", callback_data='back_to_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_text(message, reply_markup=reply_markup)

def show_subscription_options(query):
    """Show subscription options"""
    user_id = query.from_user.id
    
    if user_id in subscribed_users:
        sub_end = subscribed_users[user_id]['subscription_end']
        query.edit_message_text(f"✅ You are already subscribed until {sub_end.strftime('%Y-%m-%d %H:%M:%S')}")
        return
    
    keyboard = [
        [InlineKeyboardButton("1 Month - $50", callback_data='subscribe_1month')],
        [InlineKeyboardButton("3 Months - $120", callback_data='subscribe_3months')],
        [InlineKeyboardButton("6 Months - $200", callback_data='subscribe_6months')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_to_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = (
        "💰 Subscription Plans:\n\n"
        "1 Month - $50\n"
        "3 Months - $120 (Save $30)\n"
        "6 Months - $200 (Save $100)\n\n"
        f"Send payment to: {WALLET_ADDRESS}\n"
        "After payment, send transaction hash to admin for approval."
    )
    
    query.edit_message_text(message, reply_markup=reply_markup)

def process_subscription(query, subscription_type):
    """Process subscription selection"""
    durations = {
        '1month': 30,
        '3months': 90,
        '6months': 180
    }
    
    duration_days = durations.get(subscription_type, 30)
    cost = SUBSCRIPTION_PRICES.get(subscription_type, 50)
    
    message = (
        f"Please send ${cost} to the following wallet address:\n\n"
        f"📍 {WALLET_ADDRESS}\n\n"
        f"After payment, forward the transaction hash to @admin for approval.\n"
        f"Your subscription will be activated within 24 hours after payment confirmation."
    )
    
    query.edit_message_text(message)

def show_help(query):
    """Show help information"""
    help_text = (
        "🤖 Victorex Trader AI Bot Help\n\n"
        "📈 Generate Signals: Get trading signals for Forex and Crypto pairs\n"
        "📊 Account Info: View your MT5 account balance and positions\n"
        "🔗 Connect MT5: Connect your MT5 account to view details\n"
        "💰 Subscribe: Purchase a subscription after trial period\n\n"
        "Commands:\n"
        "/start - Start the bot\n"
        "/subscribe - Show subscription options\n"
        "/help - Show this help message\n\n"
        "For support, contact @admin"
    )
    query.edit_message_text(help_text)

def show_admin_panel(query):
    """Show admin panel"""
    user_id = query.from_user.id
    
    if user_id != int(ADMIN_ID):
        query.edit_message_text("⛔ Access denied.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📢 Broadcast Message", callback_data='admin_broadcast')],
        [InlineKeyboardButton("👥 Manage Users", callback_data='admin_manage_users')],
        [InlineKeyboardButton("✅ Approve Subscriptions", callback_data='admin_approve')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_to_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text("👨‍💼 Admin Panel", reply_markup=reply_markup)

def start_from_query(query):
    """Start command handler for query callbacks"""
    user_id = query.from_user.id
    
    if user_id in banned_users:
        query.edit_message_text("⛔ Your account has been banned. Contact admin for support.")
        return
    
    if user_id not in users:
        users[user_id] = {
            'start_date': datetime.now(),
            'trial_end': datetime.now() + timedelta(days=TRIAL_DAYS),
            'subscribed': False,
            'subscription_end': None
        }
        trial_users[user_id] = users[user_id]
    
    keyboard = [
        [InlineKeyboardButton("📈 Generate Signal", callback_data='generate_signal')],
        [InlineKeyboardButton("📊 Account Info", callback_data='account_info')],
        [InlineKeyboardButton("🔗 Connect MT5", callback_data='connect_mt5')],
        [InlineKeyboardButton("💰 Subscribe", callback_data='subscribe')],
        [InlineKeyboardButton("🆘 Help", callback_data='help')]
    ]
    
    if user_id == int(ADMIN_ID):
        keyboard.append([InlineKeyboardButton("👨‍💼 Admin Panel", callback_data='admin_panel')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_text(
        "🤖 Welcome to Victorex Trader AI Bot!\n\n"
        "I provide Forex and Crypto trading signals based on technical analysis.\n\n"
        f"Your trial period ends: {users[user_id]['trial_end'].strftime('%Y-%m-%d %H:%M:%S')}",
        reply_markup=reply_markup
    )

def has_access(user_id):
    """Check if user has access to signals"""
    if user_id == int(ADMIN_ID):
        return True
    
    if user_id in banned_users:
        return False
    
    if user_id in subscribed_users:
        return True
    
    if user_id in trial_users:
        if datetime.now() < trial_users[user_id]['trial_end']:
            return True
        else:
            # Trial expired, remove from trial users
            trial_users.pop(user_id, None)
            return False
    
    return False

def check_trials():
    """Check and remove expired trials"""
    current_time = datetime.now()
    expired_users = []
    
    for user_id, user_data in trial_users.items():
        if current_time > user_data['trial_end']:
            expired_users.append(user_id)
    
    for user_id in expired_users:
        trial_users.pop(user_id, None)

def check_subscriptions():
    """Check and remove expired subscriptions"""
    current_time = datetime.now()
    expired_users = []
    
    for user_id, user_data in subscribed_users.items():
        if current_time > user_data['subscription_end']:
            expired_users.append(user_id)
    
    for user_id in expired_users:
        subscribed_users.pop(user_id, None)
        users[user_id]['subscribed'] = False
        users[user_id]['subscription_end'] = None

def schedule_checks():
    """Run periodic checks for trials and subscriptions"""
    schedule.every().day.at("00:00").do(check_trials)
    schedule.every().day.at("00:00").do(check_subscriptions)
    
    while True:
        schedule.run_pending()
        time.sleep(60)

def main():
    """Main function to start the bot"""
    # Start the scheduler in a separate thread
    scheduler_thread = threading.Thread(target=schedule_checks, daemon=True)
    scheduler_thread.start()
    
    # Create updater and dispatcher
    updater = Updater(TOKEN)
    dispatcher = updater.dispatcher
    
    # Add handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("subscribe", lambda u, c: show_subscription_options(u.callback_query)))
    dispatcher.add_handler(CommandHandler("help", lambda u, c: show_help(u.callback_query)))
    dispatcher.add_handler(CallbackQueryHandler(button_handler))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_mt5_credentials))
    
    # Start the bot
    updater.start_polling()
    logger.info("Victorex Trader AI Bot started successfully")
    updater.idle()

if __name__ == '__main__':
    main()
