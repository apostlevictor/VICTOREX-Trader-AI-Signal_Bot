import logging
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, CallbackContext, MessageHandler, filters

# Configuration - REPLACE THESE WITH YOUR ACTUAL VALUES
BOT_TOKEN = "7783484055:AAGKrf9zDJfgBgSSG8SHJ-3lOYtzyu8qqM0"  # Get from @BotFather on Telegram
ADMIN_ID = 8367788232  # Your Telegram user ID (get from @userinfobot)
TWELVE_DATA_API_KEY = "82a9a98cc0f7401a9140f7fe0bf4e4b8"  # Get from https://twelvedata.com/
REFERRAL_LINK = "https://pocket-friends.com/r/hwgl3jonzs"  # Your referral link

# Forex pairs supported by Twelve Data API
FOREX_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", 
    "USD/CAD", "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY",
    "AUD/JPY", "EUR/CAD", "EUR/AUD", "EUR/CHF", "GBP/CHF",
    "AUD/CAD", "AUD/NZD", "CAD/JPY", "CHF/JPY", "GBP/CAD",
    "GBP/AUD", "GBP/NZD", "NZD/JPY", "USD/SEK", "USD/NOK",
    "USD/DKK", "USD/TRY", "USD/ZAR", "USD/MXN", "USD/SGD"
]

# Store user data
user_data = {}
pending_approvals = {}
banned_users = set()

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    handlers=[
        logging.FileHandler("victorex_trader.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Twelve Data API functions
def twelve_data_api_request(endpoint, params=None):
    """Make a request to the Twelve Data API"""
    try:
        base_url = "https://api.twelvedata.com"
        url = f"{base_url}/{endpoint}"
        
        if params is None:
            params = {}
        
        params["apikey"] = TWELVE_DATA_API_KEY
        
        response = requests.get(url, params=params, timeout=15)
        data = response.json()
        
        if 'code' in data and data['code'] != 200:
            logger.error(f"Twelve Data API error: {data.get('message', 'Unknown error')}")
            return None
            
        return data
    except Exception as e:
        logger.error(f"Twelve Data API request failed: {e}")
        return None

def get_time_series(symbol, interval='5min', output_size=100):
    """Get time series data for a symbol"""
    response = twelve_data_api_request("time_series", {
        "symbol": symbol,
        "interval": interval,
        "outputsize": output_size
    })
    
    if response and 'values' in response:
        return response['values']
    return None

def get_real_time_price(symbol):
    """Get real-time price for a symbol"""
    response = twelve_data_api_request("price", {
        "symbol": symbol
    })
    
    if response and 'price' in response:
        return float(response['price'])
    return None

# Technical indicator calculations
def calculate_macd(data, slow=26, fast=12, signal=9):
    try:
        closes = [float(item['close']) for item in data]
        series = pd.Series(closes)
        
        exp1 = series.ewm(span=fast).mean()
        exp2 = series.ewm(span=slow).mean()
        macd = exp1 - exp2
        signal_line = macd.ewm(span=signal).mean()
        histogram = macd - signal_line
        
        return macd.iloc[-1], signal_line.iloc[-1], histogram.iloc[-1]
    except Exception as e:
        logger.error(f"Error calculating MACD: {e}")
        return 0, 0, 0

def calculate_stochastic(data, period=14):
    try:
        highs = [float(item['high']) for item in data]
        lows = [float(item['low']) for item in data]
        closes = [float(item['close']) for item in data]
        
        high_series = pd.Series(highs)
        low_series = pd.Series(lows)
        close_series = pd.Series(closes)
        
        low_min = low_series.rolling(window=period).min()
        high_max = high_series.rolling(window=period).max()
        stoch = 100 * (close_series - low_min) / (high_max - low_min)
        
        return stoch.iloc[-1]
    except Exception as e:
        logger.error(f"Error calculating Stochastic: {e}")
        return 50  # Neutral value

def calculate_cci(data, period=20):
    try:
        highs = [float(item['high']) for item in data]
        lows = [float(item['low']) for item in data]
        closes = [float(item['close']) for item in data]
        
        high_series = pd.Series(highs)
        low_series = pd.Series(lows)
        close_series = pd.Series(closes)
        
        typical_price = (high_series + low_series + close_series) / 3
        sma = typical_price.rolling(window=period).mean()
        mean_deviation = abs(typical_price - sma).rolling(window=period).mean()
        cci = (typical_price - sma) / (0.015 * mean_deviation)
        
        return cci.iloc[-1]
    except Exception as e:
        logger.error(f"Error calculating CCI: {e}")
        return 0  # Neutral value

# Generate trading signal for binary options
def generate_signal(forex_pair, expiration_minutes):
    # Get time series data (5-minute intervals, last 100 candles)
    data = get_time_series(forex_pair, interval='5min', output_size=100)
    if data is None or len(data) < 30:
        logger.error(f"Insufficient data for {forex_pair}")
        return "❌ Error: Unable to fetch sufficient market data for analysis. Please try again later."
    
    try:
        # Calculate indicators
        macd, signal, histogram = calculate_macd(data)
        stoch = calculate_stochastic(data)
        cci = calculate_cci(data)
        
        # Get current price for reference
        current_price = get_real_time_price(forex_pair)
        
        # Generate signal based on indicators
        signal_strength = 0
        analysis = []
        
        # MACD analysis
        if macd > signal:
            signal_strength += 1
            analysis.append("MACD: Bullish crossover")
        else:
            signal_strength -= 1
            analysis.append("MACD: Bearish crossover")
        
        # Stochastic analysis
        if stoch > 80:
            signal_strength -= 1
            analysis.append("Stochastic: Overbought")
        elif stoch < 20:
            signal_strength += 1
            analysis.append("Stochastic: Oversold")
        else:
            analysis.append("Stochastic: Neutral")
        
        # CCI analysis
        if cci > 100:
            signal_strength -= 1
            analysis.append("CCI: Overbought")
        elif cci < -100:
            signal_strength += 1
            analysis.append("CCI: Oversold")
        else:
            analysis.append("CCI: Neutral")
        
        # Determine final signal for binary options (CALL or PUT)
        if signal_strength > 1:
            trade_signal = "STRONG CALL"
            signal_color = "🟢"
            emoji = "📈"
        elif signal_strength > 0:
            trade_signal = "CALL"
            signal_color = "🟢"
            emoji = "📈"
        elif signal_strength < -1:
            trade_signal = "STRONG PUT"
            signal_color = "🔴"
            emoji = "📉"
        elif signal_strength < 0:
            trade_signal = "PUT"
            signal_color = "🔴"
            emoji = "📉"
        else:
            trade_signal = "WAIT"
            signal_color = "🟡"
            emoji = "⏸️"
        
        # Format response for binary options
        response = f"{signal_color} {forex_pair} FOREX  Signal {emoji}\n"
        response += f"💰 Current Price: {current_price if current_price else 'N/A'}\n"
        response += f"⏰ Expiry: {expiration_minutes} minutes\n"
        response += f"📈 Direction: {trade_signal}\n\n"
        response += "Analysis Summary:\n"
        for line in analysis:
            response += f"• {line}\n"
        
        response += f"\n⏳ Expires: {(datetime.now() + timedelta(minutes=expiration_minutes)).strftime('%H:%M')}"
        response += f"\n🕒 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        response += "\n\n⚡ Powered by Victorex Trader"
        response += "\n\n⚠️ RISK WARNING: FOREX trading carries significant risk. Past performance doesn't guarantee future results."
        
        return response
        
    except Exception as e:
        logger.error(f"Error generating signal for {forex_pair}: {e}")
        return "❌ Error: Technical issue in signal generation. Please try again later."

# Start command
async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    # Check if user is banned
    if user_id in banned_users:
        await update.message.reply_text("❌ You are banned from using this bot.")
        return
    
    keyboard = []
    
    if user_id == ADMIN_ID:
        # Admin menu
        keyboard = [
            [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")],
            [InlineKeyboardButton("👥 User Management", callback_data="admin_users")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
            [InlineKeyboardButton("📈 Generate Signal", callback_data="select_asset")],
            [InlineKeyboardButton("ℹ️ About", callback_data="about")]
        ]
    elif user_id in user_data and user_data[user_id].get('approved', False):
        # Approved user menu
        keyboard = [
            [InlineKeyboardButton("📈 Get Signal", callback_data="select_asset")],
            [InlineKeyboardButton("📚 Trading Tips", callback_data="tips")],
            [InlineKeyboardButton("🆘 Support", callback_data="support")],
            [InlineKeyboardButton("ℹ️ About", callback_data="about")]
        ]
    else:
        # New user menu
        keyboard = [
            [InlineKeyboardButton("🔓 Get Access", callback_data="get_access")],
            [InlineKeyboardButton("📚 Trading Tips", callback_data="tips")],
            [InlineKeyboardButton("🆘 Support", callback_data="support")],
            [InlineKeyboardButton("ℹ️ About", callback_data="about")]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🏆 Welcome to Victorex Trader Signals!\n\n"
        "We provide high-quality FOREX signals based on technical analysis.\n\n"
        "Start by getting access to our signals!",
        reply_markup=reply_markup
    )

# Handle callback queries
async def handle_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id
    
    # Check if user is banned
    if user_id in banned_users:
        await query.answer("❌ You are banned from using this bot.", show_alert=True)
        return
    
    await query.answer()
    
    if query.data == "get_access":
        if user_id in user_data and user_data[user_id].get('approved', False):
            await query.edit_message_text("✅ You already have access!")
            return
        
        # Request registration
        if user_id not in pending_approvals:
            pending_approvals[user_id] = {
                "username": query.from_user.username,
                "first_name": query.from_user.first_name,
                "last_name": query.from_user.last_name,
                "join_date": datetime.now().strftime("%Y-%m-%d %H:%M")
            }
            
            # Notify admin
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"📥 New registration request:\n"
                    f"👤 User: {query.from_user.first_name} {query.from_user.last_name}\n"
                    f"📛 Username: @{query.from_user.username}\n"
                    f"🆔 ID: {user_id}\n"
                    f"📅 Joined: {pending_approvals[user_id]['join_date']}\n\n"
                    f"Use /approve_{user_id} to approve this user."
                )
            except:
                logger.error("Could not notify admin")
        
        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🔓 To get access to Victorex Trader signals:\n\n"
            f"1. Register using our referral link:\n{REFERRAL_LINK}\n\n"
            f"2. After registration, send your referral ID here\n\n"
            f"3. Wait for admin approval (usually within 24 hours)\n\n"
            f"📞 Need help? Contact @VICTOREXTRADER_BOT for support",
            reply_markup=reply_markup
        )
    
    elif query.data == "select_asset":
        if user_id != ADMIN_ID and (user_id not in user_data or not user_data[user_id].get('approved', False)):
            await query.edit_message_text("❌ You need to get access first!")
            return
            
        # Forex pairs selection (organized in rows of 3)
        keyboard = []
        row = []
        for i, pair in enumerate(FOREX_PAIRS):
            row.append(InlineKeyboardButton(pair, callback_data=f"asset_{pair}"))
            if (i + 1) % 3 == 0 or i == len(FOREX_PAIRS) - 1:
                keyboard.append(row)
                row = []
        
        keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("📊 Select a Forex pair:", reply_markup=reply_markup)
    
    elif query.data.startswith("asset_"):
        asset = query.data.split("_", 1)[1]
        context.user_data['selected_asset'] = asset
        
        # Expiration selection for binary options
        keyboard = [
            [InlineKeyboardButton("1 min", callback_data="exp_1")],
            [InlineKeyboardButton("3 min", callback_data="exp_5")],
            [InlineKeyboardButton("5 min", callback_data="exp_5")],
            [InlineKeyboardButton("15 min", callback_data="exp_15")],
            [InlineKeyboardButton("30 min", callback_data="exp_30")],
            [InlineKeyboardButton("1 hour", callback_data="exp_60")],
            [InlineKeyboardButton("4 hours", callback_data="exp_240")],
            [InlineKeyboardButton("🔙 Back to Assets", callback_data="select_asset")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"Selected: {asset}\n⏰ Choose expiration time:", reply_markup=reply_markup)
    
    elif query.data.startswith("exp_"):
        expiration = int(query.data.split("_")[1])
        asset = context.user_data.get('selected_asset', 'EUR/USD')
        
        # Generate and send signal
        signal = generate_signal(asset, expiration)
        await query.edit_message_text(signal)
        
        # Schedule signal expiration for non-admin users
        if user_id != ADMIN_ID:
            context.job_queue.run_once(
                expire_signal, 
                expiration * 60, 
                data=user_id,
                name=f"expire_{user_id}"
            )
    
    elif query.data == "tips":
        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        tips_text = (
            "📚  FOREX Trading Tips:\n\n"
            "🎯 Risk Management:\n"
            "• Never invest more than 2-3% of your capital on a single trade\n"
            "• Use stop-loss orders to limit losses\n"
            "• Set profit targets and stick to them\n\n"
            "💡 Trading Strategies:\n"
            "• Combine multiple timeframes for better accuracy\n"
            "• Trade during high volatility periods (market openings)\n"
            "• Avoid trading during news events if you're a beginner\n\n"
            "⚠️ Important:\n"
            "• forex and binary options trading carries significant risk\n"
            "• Past performance doesn't guarantee future results\n"
            "• Only trade with money you can afford to lose\n\n"
            "📖 Education is key to success in trading!"
        )
        
        await query.edit_message_text(tips_text, reply_markup=reply_markup)
    
    elif query.data == "support":
        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🆘 Need help or have questions?\n\n"
            "📞 Contact our support team: @VICTOREXTRADER_BOT\n\n"
            "We're here to assist you with:\n"
            "• Account issues\n"
            "• Signal questions\n"
            "• Technical support\n"
            "• Trading advice\n\n"
            "We typically respond within 24 hours.",
            reply_markup=reply_markup
        )
    
    elif query.data == "about":
        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        about_text = (
            "🏆 Victorex Trader Signals\n\n"
            "We are a professional trading signal provider specializing in "
            "FOREX trading. Our signals are generated using advanced "
            "trading analysis including MACD, Stochastic, and CCI indicators.\n\n"
            "📊 Our Approach:\n"
            "• Real-time market analysis\n"
            "• Multiple timeframe confirmation\n"
            "• Risk management guidelines\n"
            "• Continuous strategy improvement\n\n"
            "⚡ Features:\n"
            "• High accuracy signals\n"
            "• Multiple forex pairs\n"
            "• Various expiration times\n"
            "• Detailed analysis\n\n"
            "⚠️ Disclaimer: Trading Forex options involves significant risk of "
            "capital loss. Past performance doesn't guarantee future results."
        )
        
        await query.edit_message_text(about_text, reply_markup=reply_markup)
    
    elif query.data == "admin_stats":
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Unauthorized access!")
            return
            
        total_users = len(user_data)
        approved_users = sum(1 for u in user_data.values() if u.get('approved', False))
        pending_count = len(pending_approvals)
        banned_count = len(banned_users)
        
        stats_text = (
            f"📊 Victorex Trader Statistics\n\n"
            f"👥 Total Users: {total_users}\n"
            f"✅ Approved Users: {approved_users}\n"
            f"⏳ Pending Approvals: {pending_count}\n"
            f"❌ Banned Users: {banned_count}\n\n"
            f"📈 Signals Generated: {sum(1 for u in user_data.values() if 'signals' in u)}\n"
            f"🕒 Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Admin Menu", callback_data="admin_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(stats_text, reply_markup=reply_markup)
    
    elif query.data == "admin_users":
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Unauthorized access!")
            return
            
        keyboard = [
            [InlineKeyboardButton("👁️ View Users", callback_data="admin_view_users")],
            [InlineKeyboardButton("✅ Approve Users", callback_data="admin_approve")],
            [InlineKeyboardButton("❌ Ban User", callback_data="admin_ban")],
            [InlineKeyboardButton("🔓 Unban User", callback_data="admin_unban")],
            [InlineKeyboardButton("🔙 Admin Menu", callback_data="admin_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text("👥 User Management Panel", reply_markup=reply_markup)
    
    elif query.data == "admin_view_users":
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Unauthorized access!")
            return
            
        if not user_data:
            await query.edit_message_text("No users registered yet.")
            return
            
        users_text = "👥 Registered Users:\n\n"
        for uid, data in user_data.items():
            status = "✅" if data.get('approved', False) else "⏳"
            users_text += f"{status} ID: {uid} | Name: {data.get('first_name', '')} {data.get('last_name', '')}\n"
            
            if len(users_text) > 3000:  # Telegram message limit
                users_text += "\n... (message too long, showing first部分)"
                break
        
        keyboard = [[InlineKeyboardButton("🔙 User Management", callback_data="admin_users")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(users_text, reply_markup=reply_markup)
    
    elif query.data == "admin_approve":
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Unauthorized access!")
            return
            
        if not pending_approvals:
            await query.edit_message_text("No pending approvals.")
            return
            
        keyboard = []
        for uid, user_info in pending_approvals.items():
            btn_text = f"{user_info['first_name']} {user_info.get('last_name', '')} (@{user_info.get('username', 'none')})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"approve_{uid}")])
        
        keyboard.append([InlineKeyboardButton("🔙 User Management", callback_data="admin_users")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text("Select user to approve:", reply_markup=reply_markup)
    
    elif query.data.startswith("approve_"):
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Unauthorized access!")
            return
            
        approve_id = int(query.data.split("_")[1])
        if approve_id not in pending_approvals:
            await query.edit_message_text("User not found in pending approvals.")
            return
            
        # Approve user
        if approve_id not in user_data:
            user_data[approve_id] = {
                "approved": True,
                "username": pending_approvals[approve_id]['username'],
                "first_name": pending_approvals[approve_id]['first_name'],
                "last_name": pending_approvals[approve_id].get('last_name', ''),
                "join_date": pending_approvals[approve_id]['join_date'],
                "approval_date": datetime.now().strftime("%Y-%m-%d %H:%M")
            }
        else:
            user_data[approve_id]["approved"] = True
            user_data[approve_id]["approval_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            
        # Remove from pending
        user_info = pending_approvals.pop(approve_id)
        
        # Notify user
        try:
            await context.bot.send_message(
                approve_id,
                "🎉 Your account has been approved!\n\n"
                "You now have full access to Victorex Trader Signals.\n\n"
                "Start by selecting a Forex pair to get your first signal!"
            )
        except:
            logger.error(f"Could not notify user {approve_id}")
        
        await query.edit_message_text(f"✅ Approved user: {user_info['first_name']}")
    
    elif query.data == "admin_ban":
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Unauthorized access!")
            return
            
        keyboard = []
        for uid, data in user_data.items():
            if uid not in banned_users:
                btn_text = f"{data.get('first_name', '')} {data.get('last_name', '')} (@{data.get('username', 'none')})"
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"ban_{uid}")])
        
        if not keyboard:
            await query.edit_message_text("No users to ban.")
            return
            
        keyboard.append([InlineKeyboardButton("🔙 User Management", callback_data="admin_users")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text("Select user to ban:", reply_markup=reply_markup)
    
    elif query.data.startswith("ban_"):
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Unauthorized access!")
            return
            
        ban_id = int(query.data.split("_")[1])
        if ban_id not in user_data:
            await query.edit_message_text("User not found.")
            return
            
        banned_users.add(ban_id)
        
        # Notify user
        try:
            await context.bot.send_message(
                ban_id,
                "❌ Your access to Victorex Trader Signals has been revoked.\n\n"
                "If you believe this is a mistake, please contact @VICTOREXTRADER_BOT"
            )
        except:
            logger.error(f"Could not notify user {ban_id}")
        
        await query.edit_message_text(f"✅ Banned user: {user_data[ban_id].get('first_name', 'Unknown')}")
    
    elif query.data == "admin_unban":
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Unauthorized access!")
            return
            
        if not banned_users:
            await query.edit_message_text("No banned users.")
            return
            
        keyboard = []
        for uid in banned_users:
            if uid in user_data:
                btn_text = f"{user_data[uid].get('first_name', '')} {user_data[uid].get('last_name', '')} (@{user_data[uid].get('username', 'none')})"
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"unban_{uid}")])
        
        keyboard.append([InlineKeyboardButton("🔙 User Management", callback_data="admin_users")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text("Select user to unban:", reply_markup=reply_markup)
    
    elif query.data.startswith("unban_"):
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Unauthorized access!")
            return
            
        unban_id = int(query.data.split("_")[1])
        if unban_id not in banned_users:
            await query.edit_message_text("User not found in banned list.")
            return
            
        banned_users.remove(unban_id)
        
        # Notify user
        try:
            await context.bot.send_message(
                unban_id,
                "✅ Your access to Victorex Trader Signals has been restored.\n\n"
                "Welcome back!"
            )
        except:
            logger.error(f"Could not notify user {unban_id}")
        
        await query.edit_message_text(f"✅ Unbanned user: {user_data[unban_id].get('first_name', 'Unknown')}")
    
    elif query.data == "admin_broadcast":
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Unauthorized access!")
            return
            
        await query.edit_message_text(
            "📢 Broadcast Message\n\n"
            "Please send the message you want to broadcast to all approved users.\n\n"
            "Type /cancel to cancel this operation."
        )
        context.user_data['awaiting_broadcast'] = True
    
    elif query.data == "admin_menu" or query.data == "main_menu":
        keyboard = []
        
        if user_id == ADMIN_ID:
            keyboard = [
                [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")],
                [InlineKeyboardButton("👥 User Management", callback_data="admin_users")],
                [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
                [InlineKeyboardButton("📈 Generate Signal", callback_data="select_asset")],
                [InlineKeyboardButton("ℹ️ About", callback_data="about")]
            ]
        elif user_id in user_data and user_data[user_id].get('approved', False):
            keyboard = [
                [InlineKeyboardButton("📈 Get Signal", callback_data="select_asset")],
                [InlineKeyboardButton("📚 Trading Tips", callback_data="tips")],
                [InlineKeyboardButton("🆘 Support", callback_data="support")],
                [InlineKeyboardButton("ℹ️ About", callback_data="about")]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("🔓 Get Access", callback_data="get_access")],
                [InlineKeyboardButton("📚 Trading Tips", callback_data="tips")],
                [InlineKeyboardButton("🆘 Support", callback_data="support")],
                [InlineKeyboardButton("ℹ️ About", callback_data="about")]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "🏆 Victorex Trader Main Menu",
            reply_markup=reply_markup
        )

# Handle text messages (for referral IDs and broadcast messages)
async def handle_message(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    text = update.message.text
    
    # Check if user is banned
    if user_id in banned_users:
        await update.message.reply_text("❌ You are banned from using this bot.")
        return
    
    # Handle broadcast message from admin
    if user_id == ADMIN_ID and context.user_data.get('awaiting_broadcast', False):
        context.user_data['awaiting_broadcast'] = False
        
        # Send to all approved users
        sent_count = 0
        for uid, data in user_data.items():
            if data.get('approved', False) and uid not in banned_users:
                try:
                    await context.bot.send_message(
                        uid,
                        f" Victorex Trader:\n\n{text}"
                    )
                    sent_count += 1
                except:
                    logger.error(f"Could not send broadcast to user {uid}")
        
        await update.message.reply_text(f"✅ Broadcast sent to {sent_count} users.")
        return
    
    if user_id in pending_approvals:
        # Store referral ID
        pending_approvals[user_id]['referral_id'] = text
        
        # Notify admin
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"📋 User {pending_approvals[user_id]['first_name']} submitted referral ID: {text}\n\n"
                f"Use /approve_{user_id} to approve this user."
            )
        except:
            logger.error("Could not notify admin")
        
        await update.message.reply_text("✅ Thank you! Your referral ID has been recorded. Please wait for admin approval.")
    else:
        await update.message.reply_text("Please use the menu buttons to interact with the bot.")

# Signal expiration job
async def expire_signal(context: CallbackContext):
    user_id = context.job.data
    try:
        await context.bot.send_message(
            user_id,
            "⏰ Your signal access has expired. Please register to get more signals."
        )
    except:
        logger.error(f"Could not notify user {user_id} about expired signal")

# Admin command to approve users
async def approve_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!")
        return
        
    if not context.args:
        await update.message.reply_text("Usage: /approve USER_ID")
        return
        
    try:
        approve_id = int(context.args[0])
        if approve_id not in pending_approvals:
            await update.message.reply_text("User not found in pending approvals.")
            return
            
        # Approve user
        if approve_id not in user_data:
            user_data[approve_id] = {
                "approved": True,
                "username": pending_approvals[approve_id]['username'],
                "first_name": pending_approvals[approve_id]['first_name'],
                "last_name": pending_approvals[approve_id].get('last_name', ''),
                "join_date": pending_approvals[approve_id]['join_date'],
                "approval_date": datetime.now().strftime("%Y-%m-%d %H:%M")
            }
        else:
            user_data[approve_id]["approved"] = True
            user_data[approve_id]["approval_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            
        # Remove from pending
        user_info = pending_approvals.pop(approve_id)
        
        # Notify user
        try:
            await context.bot.send_message(
                approve_id,
                "🎉 Your account has been approved!\n\n"
                "You now have full access to Victorex Trader Signals.\n\n"
                "Start by selecting a Forex pair to get your first signal!"
            )
        except:
            logger.error(f"Could not notify user {approve_id}")
        
        await update.message.reply_text(f"✅ Approved user: {user_info['first_name']}")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

# Admin command to ban users
async def ban_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!")
        return
        
    if not context.args:
        await update.message.reply_text("Usage: /ban USER_ID")
        return
        
    try:
        ban_id = int(context.args[0])
        if ban_id not in user_data:
            await update.message.reply_text("User not found.")
            return
            
        banned_users.add(ban_id)
        
        # Notify user
        try:
            await context.bot.send_message(
                ban_id,
                "❌ Your access to Victorex Trader Signals has been revoked.\n\n"
                "If you believe this is a mistake, please contact @VICTOREXTRADER_BOT"
            )
        except:
            logger.error(f"Could not notify user {ban_id}")
        
        await update.message.reply_text(f"✅ Banned user: {user_data[ban_id].get('first_name', 'Unknown')}")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

# Admin command to unban users
async def unban_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!")
        return
        
    if not context.args:
        await update.message.reply_text("Usage: /unban USER_ID")
        return
        
    try:
        unban_id = int(context.args[0])
        if unban_id not in banned_users:
            await update.message.reply_text("User not found in banned list.")
            return
            
        banned_users.remove(unban_id)
        
        # Notify user
        try:
            await context.bot.send_message(
                unban_id,
                "✅ Your access to Victorex Trader Signals has been restored.\n\n"
                "Welcome back!"
            )
        except:
            logger.error(f"Could not notify user {unban_id}")
        
        await update.message.reply_text(f"✅ Unbanned user: {user_data[unban_id].get('first_name', 'Unknown')}")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

# Cancel command
async def cancel_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if 'awaiting_broadcast' in context.user_data:
        context.user_data['awaiting_broadcast'] = False
        await update.message.reply_text("❌ Broadcast cancelled.")
    else:
        await update.message.reply_text("No operation to cancel.")

# Error handler
async def error_handler(update: Update, context: CallbackContext):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# Main function
def main():
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("approve", approve_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    # Start the bot
    application.run_polling()
    logger.info("Bot is running...")

if __name__ == "__main__":
    main()
