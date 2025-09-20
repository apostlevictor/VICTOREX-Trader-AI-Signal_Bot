import os
import logging
import json
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
from telegram import InputMediaPhoto
import matplotlib.pyplot as plt
from io import BytesIO
import asyncio
from ta.trend import MACD, EMAIndicator, ADXIndicator, IchimokuIndicator
from ta.momentum import RSIIndicator, StochasticOscillator, TSIIndicator
from ta.volatility import BollingerBands, KeltnerChannel, AverageTrueRange
from ta.volume import VolumeWeightedAveragePrice, OnBalanceVolumeIndicator
import schedule
import time
from threading import Thread
import pytz
from PIL import Image, ImageDraw, ImageFont
import random

# Configure bot settings directly in the code
TELEGRAM_TOKEN = "8269947031:AAEbJeffGvWU0AlKeqSlTSxabKacLPWLT4M"  # Replace with your bot token
POLYGON_API_KEY = "oW9UWpfhEkDzXmMa0x1x9o8S5pViL0B7"    # Replace with your Polygon.io API key
ADMIN_ID = "836778Upda"             # Replace with your Telegram user ID
POCKET_OPTION_REFERRAL = "https://pocketoption.com/en/referral/12345"  # Replace with your referral link

# Nigeria timezone
NIGERIA_TZ = pytz.timezone('Africa/Lagos')

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
ASSET_SELECTION, TIMEFRAME_SELECTION, GENERATING_SIGNAL, TRADING_LESSONS = range(4)
ADMIN_APPROVAL = range(1)

# Assets for trading
OTC_ASSETS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", 
    "TSLA", "NVDA", "JPM", "JNJ", "V",
    "WMT", "PG", "MA", "DIS", "BAC",
    "XOM", "CSCO", "VZ", "ADBE", "INTC",
    "CMCSA", "PFE", "NFLX", "CRM", "PYPL"
]

CURRENCY_ASSETS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", 
    "AUD/USD", "USD/CAD", "NZD/USD", "EUR/GBP",
    "EUR/JPY", "GBP/JPY", "AUD/JPY", "EUR/AUD",
    "EUR/CAD", "EUR/CHF", "GBP/AUD", "GBP/CAD"
]

ALL_ASSETS = OTC_ASSETS + CURRENCY_ASSETS

# Trading lessons
TRADING_LESSONS = [
    {
        "title": "Lesson 1: Understanding Options Trading",
        "content": """Options trading involves buying and selling contracts that give the holder the right to buy or sell an asset at a specific price before a certain date.

Key Concepts:
- Call Option: Right to buy an asset
- Put Option: Right to sell an asset
- Strike Price: Price at which the asset can be bought/sold
- Expiration Date: Date when the option contract expires

Always remember: Higher risk comes with higher potential rewards, but also higher potential losses."""
    },
    {
        "title": "Lesson 2: Technical Analysis Basics",
        "content": """Technical analysis involves studying price charts and using indicators to predict future price movements.

Common Indicators:
- RSI (Relative Strength Index): Measures speed and change of price movements
- MACD (Moving Average Convergence Divergence): Shows relationship between two moving averages
- Bollinger Bands: Volatility bands placed above and below a moving average

These indicators help identify trends, momentum, and potential reversal points."""
    },
    {
        "title": "Lesson 3: Risk Management",
        "content": """Proper risk management is crucial for successful trading.

Risk Management Rules:
1. Never risk more than 2-3% of your capital on a single trade
2. Use stop-loss orders to limit potential losses
3. Take profits at predetermined levels
4. Diversify your trades across different assets

Remember: The goal is to preserve capital while growing your account steadily."""
    },
    {
        "title": "Lesson 4: Market Psychology",
        "content": """Understanding market psychology can give you an edge in trading.

Key Psychological Factors:
- Fear and Greed: The two primary emotions that drive markets
- Herd Mentality: Traders often follow the crowd, creating trends
- FOMO (Fear Of Missing Out): Can lead to impulsive decisions
- Confirmation Bias: Seeking information that confirms existing beliefs

Successful traders control their emotions and stick to their strategies."""
    },
    {
        "title": "Lesson 5: Developing a Trading Plan",
        "content": """A trading plan is essential for consistent results.

Elements of a Trading Plan:
1. Clear entry and exit criteria
2. Risk management rules
3. Position sizing strategy
4. Trading hours and schedule
5. Record keeping and review process

Stick to your plan and avoid making impulsive decisions based on emotions."""
    }
]

# User management
def load_user_data():
    try:
        with open('user_data.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"users": {}, "pending_approval": [], "subscriptions": {}}

def save_user_data(data):
    with open('user_data.json', 'w') as f:
        json.dump(data, f, indent=4)

# Initialize user data
user_data = load_user_data()

# Check if user is approved
def is_approved(user_id):
    return str(user_id) in user_data["users"] and user_data["users"][str(user_id)].get("approved", False)

# Check if user is admin
def is_admin(user_id):
    return str(user_id) == ADMIN_ID

# Check if user has active subscription
def has_subscription(user_id):
    if is_admin(user_id):
        return True
    user_id_str = str(user_id)
    if user_id_str in user_data["subscriptions"]:
        expiry_date = datetime.fromisoformat(user_data["subscriptions"][user_id_str]["expiry_date"])
        return expiry_date > datetime.now()
    return False

# Technical analysis functions
def fetch_realtime_data(symbol, timeframe_minutes=5):
    """Fetch real-time data from Polygon.io API"""
    try:
        # Calculate start and end times for the requested timeframe
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=timeframe_minutes * 100)  # Get data for last 100 periods
        
        # Format dates for API call
        from_date = start_time.strftime('%Y-%m-%d')
        to_date = end_time.strftime('%Y-%m-%d')
        
        # For forex pairs, we need to format differently
        if "/" in symbol:
            symbol = "C:" + symbol.replace("/", "")
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/minute/{from_date}/{to_date}?adjusted=true&sort=asc&limit=100&apiKey={POLYGON_API_KEY}"
        
        response = requests.get(url)
        data = response.json()
        
        if data['status'] == 'OK' and data['resultsCount'] > 0:
            df = pd.DataFrame(data['results'])
            df['timestamp'] = pd.to_datetime(df['t'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        else:
            # Fallback to previous method if no data
            return fetch_fallback_data(symbol, timeframe_minutes)
    except Exception as e:
        logger.error(f"Error fetching real-time data: {e}")
        return fetch_fallback_data(symbol, timeframe_minutes)

def fetch_fallback_data(symbol, timeframe_minutes):
    """Fallback method to fetch data if real-time fails"""
    try:
        # For forex pairs, we need to format differently
        if "/" in symbol:
            symbol = "C:" + symbol.replace("/", "")
        
        # Get current date
        to_date = datetime.now().strftime('%Y-%m-%d')
        from_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/minute/{from_date}/{to_date}?adjusted=true&sort=asc&limit=500&apiKey={POLYGON_API_KEY}"
        
        response = requests.get(url)
        data = response.json()
        
        if data['status'] == 'OK' and data['resultsCount'] > 0:
            df = pd.DataFrame(data['results'])
            df['timestamp'] = pd.to_datetime(df['t'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df.iloc[-100:]  # Return only the last 100 periods
        return None
    except Exception as e:
        logger.error(f"Error in fallback data fetch: {e}")
        return None

def calculate_advanced_indicators(df):
    """Calculate advanced technical indicators"""
    try:
        # RSI
        rsi = RSIIndicator(close=df['c'], window=14)
        df['rsi'] = rsi.rsi()
        
        # MACD
        macd = MACD(close=df['c'])
        df['macd'] = macd.macd()
        df['macd_signal'] = macd.macd_signal()
        df['macd_diff'] = macd.macd_diff()
        
        # Bollinger Bands
        bb = BollingerBands(close=df['c'], window=20, window_dev=2)
        df['bb_upper'] = bb.bollinger_hband()
        df['bb_middle'] = bb.bollinger_mband()
        df['bb_lower'] = bb.bollinger_lband()
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle']
        
        # Stochastic Oscillator
        stoch = StochasticOscillator(high=df['h'], low=df['l'], close=df['c'], window=14, smooth_window=3)
        df['stoch_k'] = stoch.stoch()
        df['stoch_d'] = stoch.stoch_signal()
        
        # EMA
        ema_12 = EMAIndicator(close=df['c'], window=12)
        ema_26 = EMAIndicator(close=df['c'], window=26)
        df['ema_12'] = ema_12.ema_indicator()
        df['ema_26'] = ema_26.ema_indicator()
        
        # ADX
        adx = ADXIndicator(high=df['h'], low=df['l'], close=df['c'], window=14)
        df['adx'] = adx.adx()
        
        # Ichimoku Cloud
        ichimoku = IchimokuIndicator(high=df['h'], low=df['l'], window1=9, window2=26, window3=52)
        df['ichimoku_a'] = ichimoku.ichimoku_a()
        df['ichimoku_b'] = ichimoku.ichimoku_b()
        
        # True Strength Index
        tsi = TSIIndicator(close=df['c'], window_slow=25, window_fast=13)
        df['tsi'] = tsi.tsi()
        
        # Keltner Channel
        keltner = KeltnerChannel(high=df['h'], low=df['l'], close=df['c'], window=20)
        df['kc_upper'] = keltner.keltner_channel_hband()
        df['kc_middle'] = keltner.keltner_channel_mband()
        df['kc_lower'] = keltner.keltner_channel_lband()
        
        # Average True Range
        atr = AverageTrueRange(high=df['h'], low=df['l'], close=df['c'], window=14)
        df['atr'] = atr.average_true_range()
        
        # Volume Weighted Average Price
        vwap = VolumeWeightedAveragePrice(high=df['h'], low=df['l'], close=df['c'], volume=df['v'], window=14)
        df['vwap'] = vwap.volume_weighted_average_price()
        
        # On Balance Volume
        obv = OnBalanceVolumeIndicator(close=df['c'], volume=df['v'])
        df['obv'] = obv.on_balance_volume()
        
        return df
    except Exception as e:
        logger.error(f"Error calculating indicators: {e}")
        return df

def generate_advanced_signal(df):
    """Generate trading signal based on multiple technical indicators"""
    if df is None or len(df) < 50:
        return "INSUFFICIENT_DATA", 0
    
    latest = df.iloc[-1]
    previous = df.iloc[-2] if len(df) > 1 else latest
    
    # Initialize scores
    buy_score = 0
    sell_score = 0
    total_indicators = 0
    
    # RSI analysis
    if latest['rsi'] < 30:
        buy_score += 3
    elif latest['rsi'] > 70:
        sell_score += 3
    elif latest['rsi'] < 40:
        buy_score += 1
    elif latest['rsi'] > 60:
        sell_score += 1
    total_indicators += 1
    
    # MACD analysis
    if latest['macd'] > latest['macd_signal'] and previous['macd'] <= previous['macd_signal']:
        buy_score += 3  # Strong buy signal on crossover
    elif latest['macd'] < latest['macd_signal'] and previous['macd'] >= previous['macd_signal']:
        sell_score += 3  # Strong sell signal on crossover
    elif latest['macd'] > latest['macd_signal']:
        buy_score += 1
    else:
        sell_score += 1
    total_indicators += 1
    
    # Bollinger Bands analysis
    if latest['c'] < latest['bb_lower']:
        buy_score += 2
    elif latest['c'] > latest['bb_upper']:
        sell_score += 2
    total_indicators += 1
    
    # EMA analysis
    if latest['ema_12'] > latest['ema_26']:
        buy_score += 2
    else:
        sell_score += 2
    total_indicators += 1
    
    # Stochastic analysis
    if latest['stoch_k'] < 20 and latest['stoch_d'] < 20:
        buy_score += 2
    elif latest['stoch_k'] > 80 and latest['stoch_d'] > 80:
        sell_score += 2
    total_indicators += 1
    
    # ADX analysis (trend strength)
    if latest['adx'] > 25:
        if buy_score > sell_score:
            buy_score += 2  # Strong trend confirmation
        else:
            sell_score += 2
    total_indicators += 1
    
    # Ichimoku Cloud analysis
    if latest['c'] > latest['ichimoku_a'] and latest['c'] > latest['ichimoku_b']:
        buy_score += 2
    elif latest['c'] < latest['ichimoku_a'] and latest['c'] < latest['ichimoku_b']:
        sell_score += 2
    total_indicators += 1
    
    # Keltner Channel analysis
    if latest['c'] > latest['kc_upper']:
        sell_score += 1
    elif latest['c'] < latest['kc_lower']:
        buy_score += 1
    total_indicators += 1
    
    # TSI analysis
    if latest['tsi'] > 0:
        buy_score += 1
    else:
        sell_score += 1
    total_indicators += 1
    
    # Determine final signal
    confidence = abs(buy_score - sell_score) / (total_indicators * 3) * 100
    
    if buy_score > sell_score + 5:
        return "STRONG_BUY", min(95, confidence + 10)
    elif buy_score > sell_score + 2:
        return "BUY", min(90, confidence + 5)
    elif sell_score > buy_score + 5:
        return "STRONG_SELL", min(95, confidence + 10)
    elif sell_score > buy_score + 2:
        return "SELL", min(90, confidence + 5)
    else:
        return "HOLD", confidence

def get_recommended_timeframe(asset):
    """Get recommended timeframe based on asset volatility"""
    # For OTC assets, recommend longer timeframes due to higher volatility
    if asset in OTC_ASSETS:
        timeframes = ['5min', '10min', '15min', '30min', '1hr']
    else:
        timeframes = ['1min', '2min', '3min', '5min', '10min']
    
    # Return a random timeframe for demo purposes (in real implementation, analyze volatility)
    return np.random.choice(timeframes)

def get_recommended_assets():
    """Get recommended assets based on market conditions"""
    # In a real implementation, this would analyze multiple assets and return the best ones
    # For demo purposes, return a mix of OTC and currency assets
    recommended = []
    
    # Add 2 OTC assets
    recommended.extend(np.random.choice(OTC_ASSETS, 2, replace=False))
    
    # Add 3 currency assets
    recommended.extend(np.random.choice(CURRENCY_ASSETS, 3, replace=False))
    
    return recommended

def create_signal_chart(df, signal, asset, timeframe, confidence):
    """Create a professional signal chart with technical analysis"""
    plt.style.use('dark_background')
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1, 1]})
    
    # Price chart with indicators
    ax1.plot(df.index, df['c'], label='Price', color='white', linewidth=1.5)
    ax1.plot(df.index, df['ema_12'], label='EMA 12', color='cyan', linewidth=1, alpha=0.7)
    ax1.plot(df.index, df['ema_26'], label='EMA 26', color='magenta', linewidth=1, alpha=0.7)
    ax1.plot(df.index, df['bb_upper'], label='BB Upper', color='red', linewidth=1, alpha=0.7, linestyle='--')
    ax1.plot(df.index, df['bb_lower'], label='BB Lower', color='green', linewidth=1, alpha=0.7, linestyle='--')
    ax1.fill_between(df.index, df['bb_upper'], df['bb_lower'], color='grey', alpha=0.1)
    ax1.set_title(f'{asset} Price Chart - {timeframe}')
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)
    
    # RSI
    ax2.plot(df.index, df['rsi'], label='RSI', color='orange', linewidth=1.5)
    ax2.axhline(70, color='red', linestyle='--', alpha=0.7)
    ax2.axhline(30, color='green', linestyle='--', alpha=0.7)
    ax2.fill_between(df.index, df['rsi'], 30, where=(df['rsi'] <= 30), color='green', alpha=0.3)
    ax2.fill_between(df.index, df['rsi'], 70, where=(df['rsi'] >= 70), color='red', alpha=0.3)
    ax2.set_ylabel('RSI')
    ax2.set_ylim(0, 100)
    ax2.grid(True, alpha=0.3)
    
    # MACD
    ax3.plot(df.index, df['macd'], label='MACD', color='blue', linewidth=1.5)
    ax3.plot(df.index, df['macd_signal'], label='Signal', color='red', linewidth=1.5)
    ax3.fill_between(df.index, df['macd'], df['macd_signal'], where=(df['macd'] > df['macd_signal']), 
                     color='green', alpha=0.3, interpolate=True)
    ax3.fill_between(df.index, df['macd'], df['macd_signal'], where=(df['macd'] <= df['macd_signal']), 
                     color='red', alpha=0.3, interpolate=True)
    ax3.set_ylabel('MACD')
    ax3.grid(True, alpha=0.3)
    ax3.legend(loc='upper left')
    
    plt.tight_layout()
    
    # Add signal text
    signal_color = 'green' if 'BUY' in signal else 'red' if 'SELL' in signal else 'orange'
    fig.text(0.5, 0.02, f"Signal: {signal} | Confidence: {confidence:.2f}% | Time: {datetime.now(NIGERIA_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}", 
             ha='center', fontsize=12, color=signal_color, weight='bold')
    
    # Save to buffer
    buf = BytesIO()
    plt.savefig(buf, format='jpg', dpi=150, bbox_inches='tight')
    buf.seek(0)
    plt.close()
    
    return buf

# Telegram bot functions
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message and referral link if not admin"""
    user_id = update.effective_user.id
    
    if is_admin(user_id):
        await update.message.reply_text(
            f"Welcome Admin!\n\n"
            f"Commands:\n"
            f"/generate - Generate custom signal\n"
            f"/recommend - Get recommended signal\n"
            f"/pending - View pending approvals\n"
            f"/users - View all users\n"
            f"/broadcast - Broadcast message to all users"
        )
    else:
        if is_approved(user_id):
            if has_subscription(user_id):
                await update.message.reply_text(
                    f"Welcome to Victorex AI Signal Bot for Option Trading!\n\n"
                    f"Commands:\n"
                    f"/generate - Generate custom signal\n"
                    f"/recommend - Get recommended signal\n"
                    f"/lessons - Trading lessons\n"
                    f"/subscription - Check your subscription status"
                )
            else:
                await update.message.reply_text(
                    f"Welcome back! Your subscription has expired.\n\n"
                    f"Please renew your subscription using our referral link:\n"
                    f"{POCKET_OPTION_REFERRAL}\n\n"
                    f"After renewing, contact admin for activation."
                )
        else:
            # Add user to pending approval if not already there
            if str(user_id) not in user_data["pending_approval"]:
                user_data["pending_approval"].append(str(user_id))
                save_user_data(user_data)
                
                # Notify admin
                if ADMIN_ID:
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"New user awaiting approval:\nID: {user_id}\nUsername: @{update.effective_user.username}"
                    )
            
            await update.message.reply_text(
                "Thank you for registering with Victorex AI Signal Bot for Option Trading! "
                "Your account is pending approval by the admin. "
                "You will be notified once approved.\n\n"
                f"Please register on Pocket Option using our referral link:\n{POCKET_OPTION_REFERRAL}\n\n"
                "After registration, please wait for admin approval to access trading signals."
          Upda
async def generate_signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the signal generation process"""
    user_id = update.effective_user.id
    
    if not is_approved(user_id) and not is_admin(user_id):
        await update.message.reply_text("Your account is pending approval. Please wait for admin approval.")
        return ConversationHandler.END
    
    if not has_subscription(user_id) and not is_admin(user_id):
        await update.message.reply_text(
            "Your subscription has expired. Please renew using our referral link:\n"
            f"{POCKET_OPTION_REFERRAL}\n\n"
            "After renewing, contact admin for activation."
        )
        return ConversationHandler.END
    
    # Create keyboard with asset options
    keyboard = []
    row = []
    
    for i, asset in enumerate(ALL_ASSETS):
        row.append(InlineKeyboardButton(asset, callback_data=f"asset_{asset}"))
        if (i + 1) % 3 == 0:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Please select an asset:", reply_markup=reply_markup)
    
    return ASSET_SELECTION

async def asset_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle asset selection"""
    query = update.callback_query
    await query.answer()
    
    asset = query.data.replace('asset_', '')
    context.user_data['asset'] = asset
    
    # Create keyboard with timeframe options
    timeframes = [
        ['1min', '2min', '3min'],
        ['5min', '10min', '15min'],
        ['30min', '1hr', '4hr']
    ]
    
    keyboard = [[InlineKeyboardButton(tf, callback_data=f"timeframe_{tf}") for tf in row] for row in timeframes]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(f"Selected asset: {asset}\nNow select timeframe:", reply_markup=reply_markup)
    
    return TIMEFRAME_SELECTION

async def timeframe_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timeframe selection"""
    query = update.callback_query
    await query.answer()
    
    timeframe = query.data.replace('timeframe_', '')
    context.user_data['timeframe'] = timeframe
    
    await query.edit_message_text(f"Generating real-time signal for {context.user_data['asset']} with {timeframe} timeframe...")
    
    # Generate signal
    asset = context.user_data['asset']
    
    # Convert timeframe to minutes for data fetching
    if timeframe == '1min':
        minutes = 1
    elif timeframe == '2min':
        minutes = 2
    elif timeframe == '3min':
        minutes = 3
    elif timeframe == '5min':
        minutes = 5
    elif timeframe == '10min':
        minutes = 10
    elif timeframe == '15min':
        minutes = 15
    elif timeframe == '30min':
        minutes = 30
    elif timeframe == '1hr':
        minutes = 60
    else:  # 4hr
        minutes = 240
    
    df = fetch_realtime_data(asset, minutes)
    
    if df is not None and len(df) > 20:
        df = calculate_advanced_indicators(df)
        signal, confidence = generate_advanced_signal(df)
        
        # Create signal chart
        chart_buf = create_signal_chart(df, signal, asset, timeframe, confidence)
        
        # Send signal with chart
        caption = f"Signal: {signal}\nAsset: {asset}\nTimeframe: {timeframe}\nConfidence: {confidence:.2f}%\nTime: {datetime.now(NIGERIA_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}"
        
        if signal in ["STRONG_BUY", "BUY"]:
            await query.message.reply_photo(photo=chart_buf, caption=caption)
            await query.message.reply_text("✅ STRONG BUY SIGNAL ✅\n\nConsider entering a CALL option")
        elif signal in ["STRONG_SELL", "SELL"]:
            await query.message.reply_photo(photo=chart_buf, caption=caption)
            await query.message.reply_text("🔻 STRONG SELL SIGNAL 🔻\n\nConsider entering a PUT option")
        else:
            await query.message.reply_photo(photo=chart_buf, caption=caption)
            await query.message.reply_text("⚠️ MARKET IN CONSOLIDATION ⚠️\n\nWait for clearer signal")
    else:
        await query.message.reply_text("Error fetching real-time data. Please try again later.")
    
    return ConversationHandler.END

async def recommend_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate recommended signal"""
    user_id = update.effective_user.id
    
    if not is_approved(user_id) and not is_admin(user_id):
        await update.message.reply_text("Your account is pending approval. Please wait for admin approval.")
        return
    
    if not has_subscription(user_id) and not is_admin(user_id):
        await update.message.reply_text(
            "Your subscription has expired. Please renew using our referral link:\n"
            f"{POCKET_OPTION_REFERRAL}\n\n"
            "After renewing, contact admin for activation."
        )
        return
    
    await update.message.reply_text("Analyzing real-time market conditions for the best opportunity...")
    
    # Get recommended assets
    recommended_assets = get_recommended_assets()
    
    best_signal = None
    best_confidence = 0
    best_asset = None
    best_timeframe = None
    best_df = None
    
    # Test each recommended asset with its optimal timeframe
    for asset in recommended_assets:
        timeframe = get_recommended_timeframe(asset)
        
        # Convert timeframe to minutes for data fetching
        if timeframe == '1min':
            minutes = 1
        elif timeframe == '2min':
            minutes = 2
        elif timeframe == '3min':
            minutes = 3
        elif timeframe == '5min':
            minutes = 5
        elif timeframe == '10min':
            minutes = 10
        elif timeframe == '15min':
            minutes = 15
        elif timeframe == '30min':
            minutes = 30
        elif timeframe == '1hr':
            minutes = 60
        else:  # 4hr
            minutes = 240
            
        df = fetch_realtime_data(asset, minutes)
        
        if df is not None and len(df) > 20:
            df = calculate_advanced_indicators(df)
            signal, confidence = generate_advanced_signal(df)
            
            if signal in ["STRONG_BUY", "STRONG_SELL"] and confidence > best_confidence:
                best_signal = signal
                best_confidence = confidence
                best_asset = asset
                best_timeframe = timeframe
                best_df = df
            elif signal in ["BUY", "SELL"] and confidence > best_confidence + 5:
                best_signal = signal
                best_confidence = confidence
                best_asset = asset
                best_timeframe = timeframe
                best_df = df
    
    # Send best signal
    if best_signal:
        # Create signal chart
        chart_buf = create_signal_chart(best_df, best_signal, best_asset, best_timeframe, best_confidence)
        
        caption = f"Recommended Signal: {best_signal}\nAsset: {best_asset}\nTimeframe: {best_timeframe}\nConfidence: {best_confidence:.2f}%\nTime: {datetime.now(NIGERIA_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}"
        
        if "BUY" in best_signal:
            await update.message.reply_photo(photo=chart_buf, caption=caption)
            await update.message.reply_text("✅ RECOMMENDED BUY SIGNAL ✅\n\nConsider entering a CALL option")
        else:
            await update.message.reply_photo(photo=chart_buf, caption=caption)
            await update.message.reply_text("🔻 RECOMMENDED SELL SIGNAL 🔻\n\nConsider entering a PUT option")
    else:
        await update.message.reply_text("No strong signals found at the moment. Market may be consolidating. Please try again later.")

async def trading_lessons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show trading lessons menu"""
    user_id = update.effective_user.id
    
    if not is_approved(user_id) and not is_admin(user_id):
        await update.message.reply_text("Your account is pending approval. Please wait for admin approval.")
        return
    
    keyboard = []
    for i, lesson in enumerate(TRADING_LESSONS):
        keyboard.append([InlineKeyboardButton(lesson['title'], callback_data=f"lesson_{i}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select a trading lesson to learn:", reply_markup=reply_markup)
    
    return TRADING_LESSONS

async def show_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show selected trading lesson"""
    query = update.callback_query
    await query.answer()
    
    lesson_idx = int(query.data.replace('lesson_', ''))
    lesson = TRADING_LESSONS[lesson_idx]
    
    # Split long messages to avoid Telegram message length limits
    message_parts = [lesson['content'][i:i+4000] for i in range(0, len(lesson['content']), 4000)]
    
    await query.edit_message_text(f"📚 {lesson['title']}\n\n{message_parts[0]}")
    
    for part in message_parts[1:]:
        await query.message.reply_text(part)
    
    # Show menu for next lesson if available
    if lesson_idx < len(TRADING_LESSONS) - 1:
        keyboard = [[InlineKeyboardButton("Next Lesson", callback_data=f"lesson_{lesson_idx+1}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Continue learning:", reply_markup=reply_markup)
        return TRADING_LESSONS
    else:
        await query.message.reply_text("You've completed all lessons! Use /generate to start trading.")
        return ConversationHandler.END

async def subscription_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show subscription information"""
    user_id = update.effective_user.id
    
    if is_admin(user_id):
        await update.message.reply_text("You are an admin with full access.")
        return
    
    if not is_approved(user_id):
        await update.message.reply_text("Your account is pending approval. Please wait for admin approval.")
        return
    
    if has_subscription(user_id):
        expiry_date = datetime.fromisoformat(user_data["subscriptions"][str(user_id)]["expiry_date"])
        days_left = (expiry_date - datetime.now()).days
        await update.message.reply_text(
            f"Your subscription is active!\n"
            f"Expiry date: {expiry_date.strftime('%Y-%m-%d')}\n"
            f"Days remaining: {days_left}\n\n"
            f"Thank you for using Victorex AI Signal Bot!"
        )
    else:
        await update.message.reply_text(
            "Your subscription has expired or you haven't subscribed yet.\n\n"
            f"Please register on Pocket Option using our referral link:\n{POCKET_OPTION_REFERRAL}\n\n"
            "After registration, contact admin to activate your subscription."
        )

async def pending_approvals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending approvals (admin only)"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("This command is for admins only.")
        return
    
    if not user_data["pending_approval"]:
        await update.message.reply_text("No pending approvals.")
        return
    
    message = "Pending approvals:\n"
    for user_id in user_data["pending_approval"]:
        message += f"User ID: {user_id}\n"
    
    message += "\nUse /approve <user_id> to approve a user."
    await update.message.reply_text(message)

async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve a user (admin only)"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("This command is for admins only.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /approve <user_id>")
        return
    
    approve_id = context.args[0]
    
    if approve_id not in user_data["pending_approval"]:
        await update.message.reply_text("User ID not found in pending approvals.")
        return
    
    # Remove from pending and add to approved users
    user_data["pending_approval"].remove(approve_id)
    user_data["users"][approve_id] = {"approved": True, "approval_date": datetime.now().isoformat()}
    
    # Add 7-day trial subscription
    expiry_date = datetime.now() + timedelta(days=7)
    user_data["subscriptions"][approve_id] = {
        "start_date": datetime.now().isoformat(),
        "expiry_date": expiry_date.isoformat(),
        "plan": "trial"
    }
    
    save_user_data(user_data)
    
    # Notify the user
    try:
        await context.bot.send_message(
            chat_id=approve_id, 
            text="🎉 Your account has been approved! 🎉\n\n"
                 "You now have access to Victorex AI Signal Bot for Option Trading.\n"
                 "You've been granted a 7-day free trial.\n\n"
                 "Use /generate to create trading signals or /recommend for our AI's top pick."
        )
    except Exception as e:
        logger.error(f"Error notifying user: {e}")
    
    await update.message.reply_text(f"User {approve_id} has been approved and granted a 7-day trial.")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all users (admin only)"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("This command is for admins only.")
        return
    
    if not user_data["users"]:
        await update.message.reply_text("No users yet.")
        return
    
    message = "Approved users:\n"
    for uid, user_info in user_data["users"].items():
        status = "Active" if has_subscription(int(uid)) else "Expired"
        message += f"User ID: {uid}, Status: {status}\n"
    
    await update.message.reply_text(message)

async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast message to all users (admin only)"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("This command is for admins only.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    
    message = " ".join(context.args)
    success_count = 0
    fail_count = 0
    
    for user_id in user_data["users"]:
        try:
            await context.bot.send_message(chat_id=user_id, text=f"📢 Admin Broadcast:\n\n{message}")
            success_count += 1
        except Exception as e:
            logger.error(f"Error sending broadcast to user {user_id}: {e}")
            fail_count += 1
    
    await update.message.reply_text(f"Broadcast completed. Success: {success_count}, Failed: {fail_count}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the current conversation"""
    await update.message.reply_text("Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def schedule_checker():
    """Check for scheduled tasks"""
    while True:
        schedule.run_pending()
        time.sleep(1)

async def send_daily_signals(context: ContextTypes.DEFAULT_TYPE):
    """Send daily signals to all subscribed users"""
    if not user_data["users"]:
        return
    
    # Get recommended signal
    recommended_assets = get_recommended_assets()
    
    best_signal = None
    best_confidence = 0
    best_asset = None
    best_timeframe = None
    best_df = None
    
    for asset in recommended_assets:
        timeframe = get_recommended_timeframe(asset)
        
        # Convert timeframe to minutes for data fetching
        if timeframe == '1min':
            minutes = 1
        elif timeframe == '2min':
            minutes = 2
        elif timeframe == '3min':
            minutes = 3
        elif timeframe == '5min':
            minutes = 5
        elif timeframe == '10min':
            minutes = 10
        elif timeframe == '15min':
            minutes = 15
        elif timeframe == '30min':
            minutes = 30
        elif timeframe == '1hr':
            minutes = 60
        else:  # 4hr
            minutes = 240
            
        df = fetch_realtime_data(asset, minutes)
        
        if df is not None and len(df) > 20:
            df = calculate_advanced_indicators(df)
            signal, confidence = generate_advanced_signal(df)
            
            if signal in ["STRONG_BUY", "STRONG_SELL"] and confidence > best_confidence:
                best_signal = signal
                best_confidence = confidence
                best_asset = asset
                best_timeframe = timeframe
                best_df = df
            elif signal in ["BUY", "SELL"] and confidence > best_confidence + 5:
                best_signal = signal
                best_confidence = confidence
                best_asset = asset
                best_timeframe = timeframe
                best_df = df
    
    if best_signal and best_df:
        # Create signal chart
        chart_buf = create_signal_chart(best_df, best_signal, best_asset, best_timeframe, best_confidence)
        
        caption = f"Daily Signal: {best_signal}\nAsset: {best_asset}\nTimeframe: {best_timeframe}\nConfidence: {best_confidence:.2f}%\nTime: {datetime.now(NIGERIA_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}"
        
        # Send to all subscribed users
        for user_id in user_data["users"]:
            if has_subscription(int(user_id)):
                try:
                    if "BUY" in best_signal:
                        await context.bot.send_photo(chat_id=user_id, photo=chart_buf, caption=caption)
                        await context.bot.send_message(chat_id=user_id, text="✅ DAILY BUY SIGNAL ✅\n\nConsider entering a CALL option")
                    else:
                        await context.bot.send_photo(chat_id=user_id, photo=chart_buf, caption=caption)
                        await context.bot.send_message(chat_id=user_id, text="🔻 DAILY SELL SIGNAL 🔻\n\nConsider entering a PUT option")
                except Exception as e:
                    logger.error(f"Error sending daily signal to user {user_id}: {e}")

def main():
    """Start the bot"""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add conversation handler for signal generation
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('generate', generate_signal_command)],
        states={
            ASSET_SELECTION: [CallbackQueryHandler(asset_selection, pattern='^asset_')],
            TIMEFRAME_SELECTION: [CallbackQueryHandler(timeframe_selection, pattern='^timeframe_')],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    # Add conversation handler for trading lessons
    lessons_handler = ConversationHandler(
        entry_points=[CommandHandler('lessons', trading_lessons)],
        states={
            TRADING_LESSONS: [CallbackQueryHandler(show_lesson, pattern='^lesson_')],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(lessons_handler)
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('recommend', recommend_signal))
    application.add_handler(CommandHandler('subscription', subscription_info))
    application.add_handler(CommandHandler('pending', pending_approvals))
    application.add_handler(CommandHandler('approve', approve_user))
    application.add_handler(CommandHandler('users', list_users))
    application.add_handler(CommandHandler('broadcast', broadcast_message))
    
    # Schedule daily signals at 9 AM Nigeria time
    schedule.every().day.at("09:00").do(lambda: asyncio.create_task(send_daily_signals(application)))
    
    # Start the scheduler thread
    scheduler_thread = Thread(target=schedule_checker)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    # Run the bot until the user presses Ctrl-C
    application.run_polling()

if __name__ == '__main__':
    main()
