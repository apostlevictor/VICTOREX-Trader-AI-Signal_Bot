import logging
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
)

# Configuration (replace with your actual credentials)
BOT_TOKEN = "7783484055:AAF86ZwHlYxPEK-doXgha4Z424nApInPfzQ"  # Get from @BotFather
ADMIN_ID = 8367788232  # Your Telegram user ID
ALPHA_VANTAGE_API_KEY = "SDZISFXTYLMXUEBW"  # Get from https://www.alphavantage.co/

# Supported currency pairs for binary options
CURRENCY_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", 
    "USD/CAD", "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY",
    "AUD/JPY", "EUR/CAD", "EUR/AUD", "EUR/CHF", "GBP/CHF",
    "AUD/CAD", "AUD/NZD", "CAD/JPY", "CHF/JPY", "GBP/CAD",
    "GBP/AUD", "GBP/NZD", "NZD/JPY", "USD/SEK", "USD/NOK",
    "USD/DKK", "USD/TRY", "USD/ZAR", "USD/MXN", "USD/SGD"
]

# Expiration time options (in minutes) for binary options
EXPIRATION_TIMES = [1, 2, 5, 15, 30, 60, 120, 240]

# User management
pending_approvals = {}  # user_id: user_data
approved_users = set()  # user_ids of approved users

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Technical Indicator Calculations
def calculate_ema(data: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average"""
    return data.ewm(span=period, adjust=False).mean()

def calculate_macd(data: pd.Series, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate MACD indicator"""
    ema_fast = calculate_ema(data, fast_period)
    ema_slow = calculate_ema(data, slow_period)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calculate_stochastic(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Stochastic Oscillator"""
    lowest_low = low.rolling(window=period).min()
    highest_high = high.rolling(window=period).max()
    stoch = 100 * (close - lowest_low) / (highest_high - lowest_low)
    return stoch

def calculate_cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    """Calculate Commodity Channel Index"""
    typical_price = (high + low + close) / 3
    sma = typical_price.rolling(window=period).mean()
    mean_deviation = typical_price.rolling(window=period).apply(
        lambda x: np.mean(np.abs(x - np.mean(x)))
    )
    cci = (typical_price - sma) / (0.015 * mean_deviation)
    return cci

def fetch_forex_data(symbol: str, interval: str = '5min', output_size: str = 'compact') -> pd.DataFrame:
    """Fetch real-time forex data from Alpha Vantage API"""
    try:
        # Alpha Vantage uses format like 'EURUSD' instead of 'EUR/USD'
        alpha_symbol = symbol.replace('/', '')
        
        url = f"https://www.alphavantage.co/query"
        params = {
            'function': 'FX_INTRADAY',
            'from_symbol': alpha_symbol[:3],
            'to_symbol': alpha_symbol[3:],
            'interval': interval,
            'outputsize': output_size,
            'apikey': ALPHA_VANTAGE_API_KEY
        }
        
        response = requests.get(url, params=params)
        data = response.json()
        
        if 'Time Series FX (' + interval + ')' not in data:
            logger.error(f"Alpha Vantage API error: {data}")
            return None
        
        time_series = data['Time Series FX (' + interval + ')']
        df = pd.DataFrame.from_dict(time_series, orient='index')
        df = df.rename(columns={
            '1. open': 'open',
            '2. high': 'high',
            '3. low': 'low',
            '4. close': 'close'
        })
        
        for col in df.columns:
            df[col] = df[col].astype(float)
        
        df = df.iloc[::-1]  # Reverse to chronological order
        return df
        
    except Exception as e:
        logger.error(f"Error fetching forex data: {e}")
        return None

def generate_signal(symbol: str, expiration_minutes: int) -> Dict:
    """Generate trading signal using technical indicators"""
    # Fetch market data
    df = fetch_forex_data(symbol)
    if df is None or len(df) < 50:
        return {"error": "Unable to fetch sufficient market data"}
    
    # Calculate indicators
    close_prices = df['close']
    high_prices = df['high']
    low_prices = df['low']
    
    # MACD
    macd_line, signal_line, histogram = calculate_macd(close_prices)
    latest_macd = macd_line.iloc[-1]
    latest_signal = signal_line.iloc[-1]
    macd_trend = "BULLISH" if latest_macd > latest_signal else "BEARISH"
    
    # Stochastic
    stoch = calculate_stochastic(high_prices, low_prices, close_prices)
    latest_stoch = stoch.iloc[-1]
    stoch_trend = "OVERSOLD" if latest_stoch < 20 else "OVERBOUGHT" if latest_stoch > 80 else "NEUTRAL"
    
    # CCI
    cci = calculate_cci(high_prices, low_prices, close_prices)
    latest_cci = cci.iloc[-1]
    cci_trend = "BULLISH" if latest_cci > 0 else "BEARISH"
    
    # Generate signal based on indicators
    signal = "CALL" if (latest_macd > latest_signal and latest_stoch < 80 and latest_cci > -100) else "PUT"
    confidence = calculate_confidence(latest_macd, latest_signal, latest_stoch, latest_cci)
    
    # Get current price
    current_price = close_prices.iloc[-1]
    
    return {
        "symbol": symbol,
        "signal": signal,
        "confidence": confidence,
        "current_price": current_price,
        "expiration": expiration_minutes,
        "indicators": {
            "MACD": {
                "value": latest_macd,
                "signal": latest_signal,
                "trend": macd_trend
            },
            "Stochastic": {
                "value": latest_stoch,
                "trend": stoch_trend
            },
            "CCI": {
                "value": latest_cci,
                "trend": cci_trend
            }
        },
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def calculate_confidence(macd: float, signal: float, stoch: float, cci: float) -> int:
    """Calculate confidence level based on indicator alignment"""
    confidence = 50  # Base confidence
    
    # MACD confidence
    macd_diff = abs(macd - signal)
    if macd_diff > 0.001:
        confidence += 10
    
    # Stochastic confidence
    if (stoch < 20 and macd > signal) or (stoch > 80 and macd < signal):
        confidence += 15
    elif (stoch > 50 and macd > signal) or (stoch < 50 and macd < signal):
        confidence += 5
    
    # CCI confidence
    if (cci > 100 and macd > signal) or (cci < -100 and macd < signal):
        confidence += 15
    elif (cci > 0 and macd > signal) or (cci < 0 and macd < signal):
        confidence += 5
    
    return min(95, max(55, confidence))  # Keep between 55-95%

# Telegram Bot Functions
def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Get appropriate keyboard based on user status"""
    if user_id not in approved_users:
        keyboard = [
            [InlineKeyboardButton("Get Access", callback_data="get_access")],
            [InlineKeyboardButton("Trading Tips/Risks", callback_data="trading_tips")],
            [InlineKeyboardButton("About Victorex Trader", callback_data="about")],
            [InlineKeyboardButton("Support", url="https://t.me/victorex_Trader")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("Generate Signal", callback_data="select_assets")],
            [InlineKeyboardButton("Trading Tips/Risks", callback_data="trading_tips")],
            [InlineKeyboardButton("About Victorex Trader", callback_data="about")],
            [InlineKeyboardButton("Support", url="https://t.me/victorex_Trader")]
        ]
    return InlineKeyboardMarkup(keyboard)

def get_assets_keyboard() -> InlineKeyboardMarkup:
    """Create keyboard for asset selection"""
    # Create buttons for currency pairs in rows of 3
    buttons = []
    row = []
    for i, pair in enumerate(CURRENCY_PAIRS):
        row.append(InlineKeyboardButton(pair, callback_data=f"asset_{i}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:  # Add any remaining buttons
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Back", callback_data="back_to_main")])
    return InlineKeyboardMarkup(buttons)

def get_expiration_keyboard() -> InlineKeyboardMarkup:
    """Create keyboard for expiration time selection"""
    buttons = []
    row = []
    for i, minutes in enumerate(EXPIRATION_TIMES):
        if minutes < 60:
            text = f"{minutes} min"
        else:
            text = f"{minutes//60} hour{'s' if minutes > 60 else ''}"
        row.append(InlineKeyboardButton(text, callback_data=f"exp_{minutes}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Back", callback_data="back_to_assets")])
    return InlineKeyboardMarkup(buttons)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message with appropriate keyboard"""
    user = update.effective_user
    user_id = user.id
    
    welcome_text = (
        "Welcome to Victorex Trader Signals Bot!\n\n"
        "We provide expert binary options trading signals based on technical analysis "
        "using MACD, Stochastic, and CCI indicators.\n\n"
        f"Your User ID: {user_id}"
    )
    
    if user_id == ADMIN_ID:
        welcome_text += "\n\nYou are logged in as Administrator."
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_keyboard(user_id)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button presses"""
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    await query.answer()
    
    if data == "get_access":
        if user_id == ADMIN_ID:
            await query.edit_message_text(
                "You are already an admin with full access!",
                reply_markup=get_main_keyboard(user_id)
            )
        elif user_id in approved_users:
            await query.edit_message_text(
                "You already have access to trading signals!",
                reply_markup=get_main_keyboard(user_id)
            )
        else:
            # Store user info for admin approval
            pending_approvals[user_id] = {
                "name": query.from_user.full_name,
                "username": query.from_user.username,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            # Notify admin
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"New user requesting access:\n"
                    f"ID: {user_id}\n"
                    f"Name: {query.from_user.full_name}\n"
                    f"Username: @{query.from_user.username}\n\n"
                    f"Use /approve_{user_id} to grant access."
                )
            except Exception as e:
                logger.error(f"Error notifying admin: {e}")
            
            await query.edit_message_text(
                "You've requested access to Victorex Trader signals.\n\n"
                "Your request has been sent for admin approval. "
                "You'll receive a notification when you're approved.\n\n"
                "For questions, contact @victorex_Trader",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Support", url="https://t.me/victorex_Trader")]
                ])
            )
    
    elif data == "select_assets" and user_id in approved_users:
        await query.edit_message_text(
            "Select a currency pair:",
            reply_markup=get_assets_keyboard()
        )
    
    elif data.startswith("asset_") and user_id in approved_users:
        asset_index = int(data.split("_")[1])
        selected_asset = CURRENCY_PAIRS[asset_index]
        context.user_data["selected_asset"] = selected_asset
        
        await query.edit_message_text(
            f"Selected: {selected_asset}\n\nNow select expiration time:",
            reply_markup=get_expiration_keyboard()
        )
    
    elif data.startswith("exp_") and user_id in approved_users:
        expiration_minutes = int(data.split("_")[1])
        selected_asset = context.user_data.get("selected_asset", "Unknown Asset")
        
        # Show generating message
        await query.edit_message_text(
            f"Generating signal for {selected_asset} with {expiration_minutes} minutes expiration...\n\nPlease wait...",
        )
        
        # Generate signal
        signal_data = generate_signal(selected_asset, expiration_minutes)
        
        if "error" in signal_data:
            await query.edit_message_text(
                f"Error generating signal: {signal_data['error']}\n\nPlease try again later.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Back to Assets", callback_data="select_assets")],
                    [InlineKeyboardButton("Main Menu", callback_data="back_to_main")]
                ])
            )
            return
        
        # Format signal message
        signal_message = (
            f"🎯 **BINARY OPTIONS SIGNAL** 🎯\n\n"
            f"**Asset:** {signal_data['symbol']}\n"
            f"**Signal:** {signal_data['signal']}\n"
            f"**Current Price:** {signal_data['current_price']:.5f}\n"
            f"**Expiration:** {signal_data['expiration']} minutes\n"
            f"**Confidence:** {signal_data['confidence']}%\n\n"
            f"**Technical Indicators:**\n"
            f"• MACD: {signal_data['indicators']['MACD']['value']:.5f} ({signal_data['indicators']['MACD']['trend']})\n"
            f"• Stochastic: {signal_data['indicators']['Stochastic']['value']:.2f} ({signal_data['indicators']['Stochastic']['trend']})\n"
            f"• CCI: {signal_data['indicators']['CCI']['value']:.2f} ({signal_data['indicators']['CCI']['trend']})\n\n"
            f"**Time:** {signal_data['timestamp']}\n\n"
            f"⚠️ **Risk Warning:** Binary options trading involves significant risk of loss. "
            f"These signals are for informational purposes only. Past performance is not indicative of future results."
        )
        
        await query.edit_message_text(
            signal_message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Generate Another", callback_data="select_assets")],
                [InlineKeyboardButton("Main Menu", callback_data="back_to_main")]
            ])
        )
    
    elif data == "trading_tips":
        tips_text = (
            "📈 **Binary Options Trading Tips & Risk Management:**\n\n"
            "1. **Risk Management:** Never risk more than 1-2% of your capital on a single trade\n"
            "2. **Stop Loss:** Always set a mental stop loss or use the platform's tools\n"
            "3. **Diversify:** Don't put all your capital in one asset or timeframe\n"
            "4. **Emotions:** Avoid emotional trading - stick to your strategy\n"
            "5. **Education:** Continuously learn about market analysis and trends\n\n"
            "**Remember:** 90% of traders lose money in binary options. "
            "Ensure you understand the risks before trading with real money.\n\n"
            "**Recommended Broker:** Pocket Option"
        )
        await query.edit_message_text(
            tips_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back", callback_data="back_to_main")]
            ])
        )
    
    elif data == "about":
        about_text = (
            "**About Victorex Trader:**\n\n"
            "Victorex Trader provides high-quality binary options trading signals "
            "using advanced technical analysis with multiple indicators "
            "including MACD, Stochastic, and CCI.\n\n"
            "Our signals are generated after thorough market analysis "
            "but remember that no signal is 100% accurate. Always practice "
            "proper risk management and only trade with money you can afford to lose.\n\n"
            "**Disclaimer:** Trading binary options involves significant risk of loss. "
            "Signals are for informational purposes only and should not be considered as financial advice."
        )
        await query.edit_message_text(
            about_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Back", callback_data="back_to_main")]
            ])
        )
    
    elif data == "back_to_main":
        await query.edit_message_text(
            "Welcome to Victorex Trader Signals Bot!",
            reply_markup=get_main_keyboard(user_id)
        )
    
    elif data == "back_to_assets":
        await query.edit_message_text(
            "Select a currency pair:",
            reply_markup=get_assets_keyboard()
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular text messages"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Admin approval command handling
    if text.startswith("/approve_") and user_id == ADMIN_ID:
        try:
            target_user_id = int(text.split("_")[1])
            if target_user_id in pending_approvals:
                approved_users.add(target_user_id)
                user_data = pending_approvals[target_user_id]
                del pending_approvals[target_user_id]
                
                # Notify the user they've been approved
                try:
                    await context.bot.send_message(
                        target_user_id,
                        "🎉 Your access has been approved!\n\n"
                        "You can now generate binary options trading signals. "
                        "Use /start to begin."
                    )
                except Exception as e:
                    logger.error(f"Error notifying user: {e}")
                
                await update.message.reply_text(
                    f"User {target_user_id} has been approved successfully."
                )
            else:
                await update.message.reply_text(
                    "User ID not found in pending approvals."
                )
        except (IndexError, ValueError):
            await update.message.reply_text(
                "Invalid format. Use /approve_USERID"
            )
    
    # Admin list command
    elif text == "/list_pending" and user_id == ADMIN_ID:
        if not pending_approvals:
            await update.message.reply_text("No pending approvals.")
        else:
            message = "Pending Approvals:\n\n"
            for uid, data in pending_approvals.items():
                message += f"ID: {uid} | Name: {data['name']} | Username: @{data.get('username', 'N/A')} | Date: {data['date']}\n"
            await update.message.reply_text(message)
    
    else:
        # For regular messages, just show the main menu
        await update.message.reply_text(
            "Please use the menu buttons to interact with the bot.",
            reply_markup=get_main_keyboard(user_id)
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates"""
    logger.error(f"Exception while handling an update: {context.error}")

def main() -> None:
    """Start the bot"""
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    # Start the bot
    print("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()
