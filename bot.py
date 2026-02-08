import asyncio
import base64
import json
import operator
import os
import platform
import random
import sys
from datetime import datetime, timedelta
from tkinter import * # Not used in core logic, but kept for completeness

import requests
from selenium.common.exceptions import ElementNotInteractableException, NoSuchElementException
from selenium.webdriver.common.by import By
import undetected_chromedriver as uc


ops = {
    '>': operator.gt,
    '<': operator.lt,
}

# --- Global Configuration ---
URL = 'https://pocket2.click/cabinet/demo-quick-high-low?utm_campaign=806509&utm_source=affiliate&utm_medium=sr&a=ovlztqbPkiBiOt&ac=github'
BASE_URL = 'https://policensor.com'  # 'http://localhost:8000'
LICENSE_URL = f'{BASE_URL}/validate_payment/'
ASSETS_URL = f'{BASE_URL}/assets/'
CANDLES_URL = f'{BASE_URL}/close_candles/'
LIMIT_TRADES_URL = f'{BASE_URL}/limit_trades/'
SERVER_STRATEGIES_URL = f'{BASE_URL}/server_strategies/'
PRODUCT_ID = 'prod_RWzyaFqdRawZim'  # 'test_00g15N2kf5IcgGk6oo'
LICENSE_BUY_URL = 'https://buy.stripe.com/3cs9Dw3W8dMT2u44gg?prefilled_email='
PERIOD = 1  # default is 1
ASSETS = {}
CANDLES = {}
ACTIONS = {}
LICENSE_VALID = None
TRADES = 0
TRADING_ALLOWED = True
CURRENT_ASSET = None
FAVORITES_REANIMATED = False
SETTINGS = {}
MARTINGALE_LIST = []
MARTINGALE_LAST_ACTION_ENDS_AT = datetime.now()
MARTINGALE_INITIAL = True # Only for initial deposit setting if needed
NUMBERS = {
    '0': '11',
    '1': '7',
    '2': '8',
    '3': '9',
    '4': '4',
    '5': '5',
    '6': '6',
    '7': '1',
    '8': '2',
    '9': '3',
}
INITIAL_DEPOSIT = None
SETTINGS_PATH = 'settings.txt'
SERVER_STRATEGIES = {}

# Martingale State Tracking (New for unconditional/forced Martingale)
LAST_TRADE_DETAILS = {'asset': None, 'action': None, 'amount': None} # Details of the last executed order
MARTINGALE_ACTIVE_ASSET = None # The asset currently locked in a Martingale series
MARTINGALE_ACTIVE_ACTION = None # The action (call/put) to be repeated in the series
MARTINGALE_STEP = 0 # Current step index in MARTINGALE_LIST (0 for initial trade)
MARTINGALE_LAST_LOSS_TIME = {} # Tracks time of last loss per asset for delay check
MARTINGALE_INITIAL_AMOUNT_SET = False # <--- NEW: Flag to prevent constant setting of $1 in Phase 3
# -----------------------------

def log(*args):
    """Logs messages with a timestamp."""
    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *args)

# --- Utility Functions ---

async def set_remote_debugging_allowed():
    """Sets RemoteDebuggingAllowed in Windows Registry for Chrome (if on Windows)."""
    os_platform = platform.platform().lower()
    if 'windows' not in os_platform:
        return
    import winreg
    key_path = r"SOFTWARE\Policies\Google\Chrome"
    value_name = "RemoteDebuggingAllowed"
    try:
        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            key_path,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY
        )
        current_value, regtype = winreg.QueryValueEx(key, value_name)
        if current_value != 1:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, 1)
            log(f"Set RemoteDebuggingAllowed to 1 in regedit")
        winreg.CloseKey(key)
    except Exception:
        pass


async def get_driver():
    """Initializes and returns an undetected_chromedriver instance."""
    options = uc.ChromeOptions()
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    options.add_argument('--ignore-ssl-errors')
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--ignore-certificate-errors-spki-list')
    options.add_argument('--disable-build-check')
    # options.add_argument('--headless=new')

    username = os.environ.get('USER', os.environ.get('USERNAME'))
    os_platform = platform.platform().lower()

    if 'macos' in os_platform:
        path_default = fr'/Users/{username}/Library/Application Support/Google/Chrome/Trading Bot Profile'
    elif 'windows' in os_platform:
        path_default = fr'C:\Users\{username}\AppData\Local\Google\Chrome\User Data\Trading Bot Profile'
    elif 'linux' in os_platform:
        path_default = '~/.config/google-chrome/Trading Bot Profile'
    else:
        path_default = ''
    options.add_argument(fr'--user-data-dir={path_default}')
    driver = uc.Chrome(options=options)
    return driver


async def get_email(driver):
    """Retrieves the user's email from the trade platform interface."""
    try:
        info_email = driver.find_element(By.CLASS_NAME, 'info__email')
        email = info_email.find_element(By.TAG_NAME, 'div').get_attribute('data-hd-show')
        if '@' not in email:
            return None
        return email
    except:
        return None


async def hand_delay():
    """Introduces a small, random delay to simulate human interaction."""
    await asyncio.sleep(random.choice([0.2, 0.3, 0.4, 0.5, 0.6]))

# --- Settings and Data Acquisition ---

def cleanup_martingale_list(value):
    """Validates and cleans the Martingale list string."""
    value = value.replace(' ', '')
    value_list = value.split(',')
    value_list = [int(v) for v in value_list]
    if len(value_list) < 2 or value_list[0] < 1 or value_list[0] > 19999 or value_list[-1] > 20000:
        raise ValueError("Invalid Martingale list format or values.")
    martingale_list = []
    for i, v in enumerate(value_list):
        if i == 0:
            martingale_list.append(v)
        elif i < len(value_list):
            if value_list[i-1] < value_list[i]:
                martingale_list.append(v)
            else:
                raise ValueError("Martingale amounts must be strictly increasing.")
    return martingale_list


def read_settings():
    """Reads settings from the settings.txt file or sets defaults."""
    global SETTINGS
    
    # Default settings for new/missing fields
    default_settings = {
        'COUNT_BULLISH': 0,
        'COUNT_BEARISH': 0,
        'FAST_MA': 5,
        'FAST_MA_TYPE': 'SMA',
        'SLOW_MA': 20,
        'SLOW_MA_TYPE': 'SMA',
        'RSI_ENABLED': False,
        'RSI_PERIOD': 14,
        'RSI_UPPER': 70,
        'RSI_CALL_SIGN': '>',
        'MARTINGALE_ENABLED': False,
        'MARTINGALE_LOSS_DELAY_SECONDS': 10, # Martingale delay
        'MARTINGALE_LIST': '1,2,4,8,16',
        'TAKE_PROFIT_ENABLED': False,
        'TAKE_PROFIT': 100,
        'STOP_LOSS_ENABLED': False,
        'STOP_LOSS': 50,
        'VICE_VERSA': False,
        'BEGINNING_CANDLE_ORDER': False,
        'USE_SERVER_STRATEGIES': False,
        'BACKTEST': False,
        'BACKTEST_TIMEFRAME': '1m',
        'MIN_PAYOUT': 70,
    }

    # Initialize with defaults
    SETTINGS.update(default_settings)
    log("Default settings loaded.")

    try:
        with open(SETTINGS_PATH, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                try:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    if key in ['FAST_MA', 'SLOW_MA', 'RSI_PERIOD', 'RSI_UPPER', 'TAKE_PROFIT', 'STOP_LOSS', 'MIN_PAYOUT', 'COUNT_BULLISH', 'COUNT_BEARISH', 'MARTINGALE_LOSS_DELAY_SECONDS']:
                        SETTINGS[key] = int(value)
                    elif key in ['RSI_CALL_SIGN']:
                        if value in ops:
                             SETTINGS[key] = value
                        else:
                            raise ValueError(f"Invalid value for {key}. Must be '>' or '<'.")
                    elif key in ['FAST_MA_TYPE', 'SLOW_MA_TYPE']:
                        if value in ['SMA', 'EMA', 'WMA']:
                            SETTINGS[key] = value
                        else:
                            raise ValueError(f"Invalid value for {key}. Must be 'SMA', 'EMA', or 'WMA'.")
                    elif key in ['MARTINGALE_LIST']:
                        SETTINGS[key] = cleanup_martingale_list(value)
                    elif key in ['RSI_ENABLED', 'MARTINGALE_ENABLED', 'TAKE_PROFIT_ENABLED', 'STOP_LOSS_ENABLED', 'VICE_VERSA', 'BEGINNING_CANDLE_ORDER', 'USE_SERVER_STRATEGIES', 'BACKTEST']:
                        SETTINGS[key] = value.lower() in ('true', '1', 't', 'y', 'yes')
                    elif key in ['BACKTEST_TIMEFRAME']:
                        SETTINGS[key] = value
                    # Ignore 'MARTINGALE_ADVANCED_ENABLED' if present, as it's not used in this fixed logic
                    elif key not in ['MARTINGALE_ADVANCED_ENABLED']:
                        log(f"Unknown setting: {key}")
                except Exception as e:
                    log(f"Error parsing setting line '{line}': {e}. Using default.")
        
        log("Settings file loaded and parsed successfully.")
        
        # Final validation and global setup
        if isinstance(SETTINGS['MARTINGALE_LIST'], str): # If it's still a string, clean it up one last time
            SETTINGS['MARTINGALE_LIST'] = cleanup_martingale_list(SETTINGS['MARTINGALE_LIST'])
        
        global MARTINGALE_LIST
        MARTINGALE_LIST = SETTINGS['MARTINGALE_LIST']

    except FileNotFoundError:
        log(f"Settings file '{SETTINGS_PATH}' not found. Using default settings.")
    except Exception as e:
        log(f"An unexpected error occurred while reading settings: {e}. Using default settings.")

# --- Candle Processing and Martingale Management (Unchanged for Core Logic) ---

async def websocket_log(driver):
    """Processes WebSocket log data to update candles and state."""
    global ASSETS, PERIOD, CANDLES, ACTIONS, LICENSE_VALID, TRADES, CURRENT_ASSET, FAVORITES_REANIMATED, \
        TRADING_ALLOWED, SERVER_STRATEGIES

    for wsData in driver.get_log('performance'):
        message = json.loads(wsData['message'])['message']
        response = message.get('params', {}).get('response', {})
        if response.get('opcode', 0) == 2:
            try:
                payload_str = base64.b64decode(response['payloadData']).decode('utf-8')
                data = json.loads(payload_str)

                if 'history' in data:
                    if not CURRENT_ASSET:
                        CURRENT_ASSET = data['asset']
                    if PERIOD != data['period']:
                        PERIOD = data['period']
                        CANDLES = {}
                        ACTIONS = {}
                        FAVORITES_REANIMATED = False
                    
                    # Process historical candles
                    candles = list(reversed(data['candles']))  # timestamp open close high low
                    for tstamp, value in data['history']:
                        tstamp = int(float(tstamp))
                        candle = [tstamp, value, value, value, value]
                        candle[2] = value  # set close all the time
                        if value > candle[3]:  # set high
                            candle[3] = value
                        if value < candle[4]:  # set low
                            candle[4] = value
                        if tstamp % PERIOD == 0:
                            if tstamp not in [c[0] for c in candles]:
                                candles.append([tstamp, value, value, value, value])
                    CANDLES[data['asset']] = candles
                
                # Process real-time updates
                try:
                    asset = data[0][0]
                    candles = CANDLES[asset]
                    current_value = data[0][2]
                    candles[-1][2] = current_value  # set close all the time
                    if current_value > candles[-1][3]:  # set high
                        candles[-1][3] = current_value
                    if current_value < candles[-1][4]:  # set low
                        candles[-1][4] = current_value
                    tstamp = int(float(data[0][1]))
                    if tstamp % PERIOD == 0:
                        if tstamp not in [c[0] for c in candles]:
                            # execute condition here
                            candles.append([tstamp, current_value, current_value, current_value, current_value])
                except:
                    pass
            except:
                pass

    if not FAVORITES_REANIMATED:
        try:
            await reanimate_favorites(driver)
        except:
            pass

    # --- [START] حذف محدودیت تعداد معامله (Remove Trade Limit) ---
    if LICENSE_VALID is None:
        try:
            # By default, we will assume a valid license to bypass server-side/daily trade limits.
            LICENSE_VALID = True
            log("License check bypassed. Assuming valid license for unlimited trades.")

            if SETTINGS.get('BACKTEST'):
                email = await get_email(driver)
                if email:
                    await backtest(email, timeframe=SETTINGS['BACKTEST_TIMEFRAME'][:-1])
                else:
                    log("Could not get email to start backtest.")

        except Exception as e:
            # Keep original error logging for unexpected issues
            print(e)
    # --- [END] حذف محدودیت تعداد معامله (Remove Trade Limit) ---

    if SETTINGS.get('USE_SERVER_STRATEGIES') and not SERVER_STRATEGIES:
        try:
            response = requests.get(SERVER_STRATEGIES_URL)
            if response.status_code == 200:
                SERVER_STRATEGIES = response.json()
                log('Server strategies downloaded')
        except Exception as e:
            log(f"Error fetching server strategies: {e}")


async def reanimate_favorites(driver):
    """Clicks on each favorite asset to ensure the bot is getting their data."""
    global CURRENT_ASSET, FAVORITES_REANIMATED

    asset_favorites_items = driver.find_elements(By.CLASS_NAME, 'assets-favorites-item')
    for item in asset_favorites_items:
        while True:
            if 'assets-favorites-item--active' in item.get_attribute('class'):
                CURRENT_ASSET = item.get_attribute('data-id')
                break
            if 'assets-favorites-item--not-active' in item.get_attribute('class'):
                break  # just skip non-active assets
            try:
                item.click()
                FAVORITES_REANIMATED = True
            except ElementNotInteractableException:
                log(f"Asset {item.get_attribute('data-id')} is out of reach. Please close some favorite assets.")
                break


async def switch_to_asset(driver, asset):
    """Switches the chart to the specified asset."""
    global CURRENT_ASSET

    asset_favorites_items = driver.find_elements(By.CLASS_NAME, 'assets-favorites-item')
    for item in asset_favorites_items:
        if item.get_attribute('data-id') != asset:  # this condition is only for single asset
            continue
        while True:
            await asyncio.sleep(0.1)
            if 'assets-favorites-item--active' in item.get_attribute('class'):
                CURRENT_ASSET = asset
                return True
            try:
                item.click()
            except:
                log(f'Asset {asset} is out of reach. Please close some favorite assets.')
                return False

    if asset == CURRENT_ASSET:
        return True  # case when favorites are closed
    
    return False # Asset not found in favorites


async def check_payout(driver, asset):
    """Checks if the current asset payout meets the minimum requirement."""
    global ACTIONS

    try:
        payout_element = driver.find_element(By.CLASS_NAME, 'value__val-start')
        payout_text = payout_element.text
        # Remove currency symbol and parse as integer (e.g., "$80%" -> 80)
        payout = int(payout_text.replace('$', '').replace('%', '').strip())
        if payout >= SETTINGS['MIN_PAYOUT']:
            return True
        log(f'Payout {payout}% is not allowed for asset {asset} (Min: {SETTINGS["MIN_PAYOUT"]}%).')
        ACTIONS[asset] = datetime.now() + timedelta(minutes=1)  # add for avoiding repeated message
        return False
    except:
        # Payout element not found or parsing failed, assume not ready or a problem
        log(f"Could not read payout for asset {asset}. Skipping trade.")
        return False


async def check_trades():
    """Bypassed: Always returns True to allow unlimited trades."""
    return True

# --- Trading Logic ---

async def set_amount_icon(driver):
    """Switches the trading amount input to be in currency (USD) instead of percentage."""
    amount_style = driver.find_element(By.CSS_SELECTOR, value='#put-call-buttons-chart-1 > div > div.blocks-wrap > div.block.block--bet-amount > div.block__control.control > div.control-buttons__wrapper > div > a')
    try:
        # Check if the currency icon (e.g., USD) is present
        amount_style.find_element(By.CLASS_NAME, value='currency-icon--usd')
    except NoSuchElementException:
        # If not, click to switch
        amount_style.click()

async def set_amount_on_ui(driver, amount):
    """Sets the trade amount on the UI using the virtual keyboard."""
    base = '#modal-root > div > div > div > div > div.trading-panel-modal__in > div.virtual-keyboard > div > div:nth-child(%s) > div'
    
    amount_element = driver.find_element(By.CSS_SELECTOR, value='#put-call-buttons-chart-1 > div > div.blocks-wrap > div.block.block--bet-amount > div.block__control.control > div.control__value.value.value--several-items > div > input[type=text]')
    
    # Clear input
    amount_element.click()
    await hand_delay()
    # Find the clear button (assuming it's number 12 in the virtual keyboard)
    try:
        clear_button = driver.find_element(By.CSS_SELECTOR, value=base % '12')
        for _ in range(5): 
            clear_button.click()
            await hand_delay()
    except:
        pass # Keyboard not visible or clear button not found

    # Enter new amount
    for number in str(amount):
        driver.find_element(By.CSS_SELECTOR, value=base % NUMBERS[number]).click()
        await hand_delay()


async def set_estimation_icon(driver):
    """Switches the expiration time style if needed."""
    time_style = driver.find_element(By.CSS_SELECTOR, value='#put-call-buttons-chart-1 > div > div.blocks-wrap > div.block.block--expiration-inputs > div.block__control.control > div.control-buttons__wrapper > div > a > div > div > svg')
    if 'exp-mode-2.svg' in time_style.get_attribute('data-src'):  # should be 'exp-mode-2.svg'
        time_style.click()  # switch time style


async def get_estimation(driver):
    """Gets the current trade expiration time in seconds."""
    estimation = driver.find_element(By.CSS_SELECTOR, value='#put-call-buttons-chart-1 > div > div.blocks-wrap > div.block.block--expiration-inputs > div.block__control.control > div.control__value.value.value--several-items')
    est = datetime.strptime(estimation.text, '%H:%M:%S')
    return (est.hour * 3600) + (est.minute * 60) + est.second


async def create_order(driver, action, asset, sstrategy=None, martingale_override=False):
    """
    Executes a trade order (Call/Put).
    martingale_override: True if this is a Martingale step trade.
    """
    global ACTIONS, MARTINGALE_LAST_ACTION_ENDS_AT, LAST_TRADE_DETAILS, \
           MARTINGALE_ACTIVE_ASSET, MARTINGALE_ACTIVE_ACTION, MARTINGALE_STEP, MARTINGALE_INITIAL_AMOUNT_SET

    # Check for trade delay
    if ACTIONS.get(asset) and ACTIONS[asset] + timedelta(seconds=PERIOD * 2) > datetime.now():
        return False
    
    # Check for asset lock (Only applies to standard trades, not Martingale overrides)
    if not martingale_override and SETTINGS.get('MARTINGALE_ENABLED') and MARTINGALE_ACTIVE_ASSET is not None:
        # Only allow trades on the active Martingale asset if it's not the Martingale trade itself
        if MARTINGALE_ACTIVE_ASSET != asset:
             log(f"Trade skipped on {asset}. Martingale series active on {MARTINGALE_ACTIVE_ASSET}.")
             return False

    try:
        # 1. Switch Asset
        switch = await switch_to_asset(driver, asset)
        if not switch:
            return False

        # 2. Check Payout
        ok_payout = await check_payout(driver, asset)
        if not ok_payout:
            return False

        # 3. Check Trade Limit (Bypassed, always True)
        trading_allowed = await check_trades()
        if not trading_allowed:
            return False

        # 4. Determine final action (Apply Vice-Versa Logic only for standard/initial trades)
        if martingale_override:
            # FIX: If it's a Martingale step, strictly use the input action (MARTINGALE_ACTIVE_ACTION)
            current_action = action
        else:
            # If it's a standard trade (Step 0), apply vice-versa logic if enabled
            vice_versa = sstrategy['vice_versa'] if sstrategy else SETTINGS['VICE_VERSA']
            current_action = 'call' if action == 'put' else 'put' if vice_versa else action

        # 5. Get and Record Amount (Crucial for Martingale)
        await set_amount_icon(driver) # Ensure we are in currency mode
        amount_element = driver.find_element(By.CSS_SELECTOR, value='#put-call-buttons-chart-1 > div > div.blocks-wrap > div.block.block--bet-amount > div.block__control.control > div.control__value.value.value--several-items > div > input[type=text]')
        amount_value = int(float((amount_element.get_attribute('value').replace(',', '').replace('$', '').replace('\u202f', ''))))

        # 6. Execute Order
        driver.find_element(by=By.CLASS_NAME, value=f'btn-{current_action}').click()
        ACTIONS[asset] = datetime.now()
        
        message = f'{current_action.capitalize()} on asset: {asset} with amount {amount_value}' # Added amount to log
        if sstrategy:
            message += f' made by server strategy with profit {sstrategy["profit"]}%'
        log(message)
        
        # 7. Martingale Logic on Order Success (Setup for result check)
        if SETTINGS.get('MARTINGALE_ENABLED'): 
            
            # Record the actual trade details for the result check (Phase 1)
            LAST_TRADE_DETAILS = {
                'asset': asset,
                'action': current_action,
                'amount': amount_value
            }
            
            # Initialize Martingale series state if this is the first trade (step 0)
            if not martingale_override and MARTINGALE_ACTIVE_ASSET is None:
                 MARTINGALE_ACTIVE_ASSET = asset
                 MARTINGALE_ACTIVE_ACTION = current_action # Store the actual action taken
                 MARTINGALE_STEP = 0
                 # Since a trade was successfully placed with the initial amount, we mark the amount as set.
                 MARTINGALE_INITIAL_AMOUNT_SET = True

            
            await set_estimation_icon(driver)
            seconds = await get_estimation(driver)
            MARTINGALE_LAST_ACTION_ENDS_AT = datetime.now() + timedelta(seconds=seconds)


    except Exception as e:
        log(f"Can't create order: {e}")
        return False
        
    return True

# --- Indicators and Strategies (Unchanged) ---

async def calculate_last_wma(candles, period):
    """Simplified/Placeholder for WMA calculation (as used in the original code's context)."""
    weights = list(range(1, period + 1))
    if len(candles) < period:
        return sum(candles) / len(candles) if candles else 0
    weighted_prices = [candles[i] * weights[i] for i in range(-period, 0)]
    return sum(weighted_prices) / sum(weights)


async def calculate_last_ema(candles, period, multiplier):
    """Simplified/Placeholder for EMA calculation (as used in the original code's context)."""
    if len(candles) < period + 10: 
        return sum(candles) / len(candles) if candles else 0
    
    sma = sum(candles[:period]) / period
    ema = sma
    
    for price in candles[period:]:
        ema = (price - ema) * multiplier + ema
        
    return ema


async def moving_averages_cross(candles, sstrategy=None):
    """Checks for a Moving Average crossover signal."""
    fast_ma = sstrategy['fast_ma'] if sstrategy else SETTINGS['FAST_MA']
    fast_ma_type = sstrategy['fast_ma_type'] if sstrategy else SETTINGS.get('FAST_MA_TYPE', 'SMA')
    slow_ma = sstrategy['slow_ma'] if sstrategy else SETTINGS['SLOW_MA']
    slow_ma_type = sstrategy['slow_ma_type'] if sstrategy else SETTINGS.get('SLOW_MA_TYPE', 'SMA')
    close_prices = [c[2] for c in candles]

    if fast_ma >= slow_ma:
        log("Moving averages 'fast' can't be bigger than or equal to 'slow'")
        return None
        
    if len(close_prices) < slow_ma + 10: # Ensure enough data for the calculation slice used below
        return None

    # Helper function to get MA value based on type
    async def get_ma(prices, period, ma_type, slice_end_offset):
        if ma_type == 'EMA':
            multiplier = 2 / (period + 1)
            full_slice = prices[:slice_end_offset]
            return await calculate_last_ema(full_slice, period, multiplier)
        elif ma_type == 'WMA':
            wma_slice = prices[:slice_end_offset]
            return await calculate_last_wma(wma_slice, period)
        else:  # SMA
            sma_slice = prices[slice_end_offset - period : slice_end_offset]
            return sum(sma_slice) / period if sma_slice else 0

    # Calculate Previous MA (for crossing detection)
    fast_ma_previous = await get_ma(close_prices, fast_ma, fast_ma_type, -2)
    slow_ma_previous = await get_ma(close_prices, slow_ma, slow_ma_type, -2)

    # Calculate Current MA (for the *current* partially formed candle)
    fast_ma_current = await get_ma(close_prices, fast_ma, fast_ma_type, -1)
    slow_ma_current = await get_ma(close_prices, slow_ma, slow_ma_type, -1)


    try:
        # Crossover UP: Fast MA crosses above Slow MA
        if fast_ma_previous < slow_ma_previous and fast_ma_current > slow_ma_current:
            return 'call'
        # Crossover DOWN: Fast MA crosses below Slow MA
        elif fast_ma_previous > slow_ma_previous and fast_ma_current < slow_ma_current:
            return 'put'
    except Exception as e:
        log(f"MA cross error: {e}")

    return None


async def get_rsi(candles, sstrategy=None):
    """Calculates the Relative Strength Index (RSI)."""
    period = sstrategy['rsi_period'] if sstrategy else SETTINGS['RSI_PERIOD']
    candles = [c[2] for c in candles]
    if len(candles) < period + 1:
        raise ValueError("Not enough data to calculate RSI.")

    gains = []
    losses = []

    # Calculate initial gains and losses
    for i in range(1, period + 1):
        delta = candles[i] - candles[i - 1]
        if delta > 0:
            gains.append(delta)
        else:
            losses.append(abs(delta))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    rsi_values = [None] * period  # Pad with None to match the length of candles

    # Calculate the first RSI value
    if avg_loss == 0:
        rsi_values.append(100)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100 - (100 / (1 + rs)))

    # Calculate subsequent RSI values
    for i in range(period + 1, len(candles)):
        delta = candles[i] - candles[i - 1]
        gain = max(delta, 0)
        loss = abs(min(delta, 0))

        # Smoothed RS calculation
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        rsi_values.append(rsi)

    return rsi_values


def get_rsi_lower(rsi_upper):
    """Calculates the corresponding RSI lower boundary."""
    return 100 - rsi_upper


def get_rsi_put_sign(call_sign):
    """Determines the appropriate sign for a 'put' action based on the 'call' sign."""
    return '<' if call_sign == '>' else '>'


async def rsi_strategy(candles, action, sstrategy=None):
    """Applies the RSI filter based on overbought/oversold levels."""
    try:
        rsi_values = await get_rsi(candles)
    except ValueError:
        return None # Not enough data

    rsi_upper = sstrategy['rsi_upper'] if sstrategy else SETTINGS.get('RSI_UPPER')
    rsi_lower = get_rsi_lower(rsi_upper)
    call_sign = sstrategy['rsi_call_sign'] if sstrategy else SETTINGS.get('RSI_CALL_SIGN')
    put_sign = get_rsi_put_sign(call_sign)
    
    current_rsi = rsi_values[-1]

    if action == 'call' and ops[call_sign](current_rsi, rsi_upper):
        return 'call'
    elif action == 'put' and ops[put_sign](current_rsi, rsi_lower):
        return 'put'
        
    return None


async def check_consecutive_candles(candles, action, sstrategy=None):
    """Checks for consecutive bullish (open < close) or bearish (open > close) candles."""
    
    # Get required counts from settings/strategy
    count_bullish = sstrategy.get('count_bullish', SETTINGS.get('COUNT_BULLISH', 0)) if sstrategy else SETTINGS.get('COUNT_BULLISH', 0)
    count_bearish = sstrategy.get('count_bearish', SETTINGS.get('COUNT_BEARISH', 0)) if sstrategy else SETTINGS.get('COUNT_BEARISH', 0)

    # We need to look at the last N *closed* candles. Skip the current, partially formed candle (candles[-1]).
    if len(candles) < max(count_bullish, count_bearish) + 1:
        return None

    if action == 'call' and count_bullish > 0:
        consecutive_bullish_count = 0
        # Iterate backwards from the second-to-last candle (last fully closed candle)
        for candle in reversed(candles[:-1]):
            # A candle is bullish if close (index 2) is greater than open (index 1)
            if candle[2] > candle[1]:
                consecutive_bullish_count += 1
            else:
                break # Stop counting
            
            if consecutive_bullish_count >= count_bullish:
                return 'call'
        return None 

    elif action == 'put' and count_bearish > 0:
        consecutive_bearish_count = 0
        # Iterate backwards from the second-to-last candle (last fully closed candle)
        for candle in reversed(candles[:-1]):
            # A candle is bearish if close (index 2) is less than open (index 1)
            if candle[2] < candle[1]:
                consecutive_bearish_count += 1
            else:
                break # Stop counting

            if consecutive_bearish_count >= count_bearish:
                return 'put'
        return None
        
    return action


async def check_strategies(candles, sstrategy=None):
    """Aggregates all active strategies (MA Cross, RSI, Consecutive Candles)."""
    
    if len(candles) < SETTINGS['SLOW_MA'] + 10: # Minimum required candles for the slowest MA/RSI to calculate
        return None

    # 1. Moving Averages Cross (Primary Signal)
    action = await moving_averages_cross(candles, sstrategy=sstrategy)
    if not action:
        return None

    # 2. RSI Filter
    rsi_enabled = True if sstrategy and sstrategy.get('rsi_period') else SETTINGS.get('RSI_ENABLED')
    if rsi_enabled:
        action = await rsi_strategy(candles, action, sstrategy=sstrategy)
        if not action:
            return None

    # 3. Consecutive Candle Count Filter
    is_count_set = SETTINGS.get('COUNT_BULLISH', 0) > 0 or SETTINGS.get('COUNT_BEARISH', 0) > 0
    
    if sstrategy and ('count_bullish' in sstrategy or 'count_bearish' in sstrategy):
        action = await check_consecutive_candles(candles, action, sstrategy=sstrategy)
        if not action:
            return None
    elif is_count_set:
        action = await check_consecutive_candles(candles, action, sstrategy=None)
        if not action:
            return None
            
    return action


async def check_deposit(driver):
    """Monitors deposit for Stop Loss and Take Profit."""
    global INITIAL_DEPOSIT, TRADING_ALLOWED

    try:
        deposit_element = driver.find_element(By.CSS_SELECTOR, value='body > div.wrapper > div.wrapper__top > header > div.right-block.js-right-block > div.right-block__item.js-drop-down-modal-open > div > div.balance-info-block__data > div.balance-info-block__balance > span')
        deposit = float(deposit_element.text.replace(',', '').replace('$', '').replace('\u202f', '')) # Clean up the value
    except Exception as e:
        log(f"Error reading deposit: {e}")
        return

    if INITIAL_DEPOSIT is None:  # set initial deposit
        INITIAL_DEPOSIT = deposit
        log(f'Initial deposit: {INITIAL_DEPOSIT}')
        await asyncio.sleep(1)
        return

    if SETTINGS.get('TAKE_PROFIT_ENABLED'):
        take_profit_level = INITIAL_DEPOSIT + SETTINGS.get('TAKE_PROFIT', 100)
        if deposit >= take_profit_level:
            log(f'Take profit reached ({take_profit_level:.2f}), trading stopped. Current deposit: {deposit:.2f}')
            TRADING_ALLOWED = False

    if SETTINGS.get('STOP_LOSS_ENABLED'):
        stop_loss_level = INITIAL_DEPOSIT - SETTINGS.get('STOP_LOSS', 50)
        if deposit <= stop_loss_level:
            log(f'Stop loss reached ({stop_loss_level:.2f}), trading stopped. Current deposit: {deposit:.2f}')
            TRADING_ALLOWED = False
            

async def check_indicators(driver):
    """Main loop for checking indicators and placing trades."""
    global MARTINGALE_LAST_ACTION_ENDS_AT, \
           LAST_TRADE_DETAILS, MARTINGALE_ACTIVE_ASSET, MARTINGALE_ACTIVE_ACTION, MARTINGALE_STEP, \
           MARTINGALE_LAST_LOSS_TIME, MARTINGALE_LIST, MARTINGALE_INITIAL_AMOUNT_SET # Added new global flag

    if not TRADING_ALLOWED:
        return

    # Check for beginning of candle order setting
    if SETTINGS.get('BEGINNING_CANDLE_ORDER'):
        now = datetime.now()
        if (now.hour * 3600 + now.minute * 60 + now.second) % PERIOD != 0:
            return

    # --- Phase 1: Martingale Step Update (Result Check) ---
    if SETTINGS.get('MARTINGALE_ENABLED') and LAST_TRADE_DETAILS['asset'] is not None:
        
        asset_name_from_trade = LAST_TRADE_DETAILS['asset']
        
        # 1. اگر زمان انقضای معامله هنوز نرسیده است، صبر کن.
        # یک بافر ۴ ثانیه‌ای برای اطمینان از به‌روزرسانی UI اضافه شده است.
        if MARTINGALE_LAST_ACTION_ENDS_AT + timedelta(seconds=4) > datetime.now():
            return
        
        log(f"MARTINGALE: Checking result for last trade on {asset_name_from_trade}...")
        
        try:
            deposit_element = driver.find_element(By.CSS_SELECTOR, value='body > div.wrapper > div.wrapper__top > header > div.right-block.js-right-block > div.right-block__item.js-drop-down-modal-open > div > div.balance-info-block__data > div.balance-info-block__balance > span')
            current_deposit = float(deposit_element.text.replace(',', '').replace('$', '').replace('\u202f', ''))
        except Exception as e:
            log(f"Error reading deposit for Martingale: {e}")
            # Assume failure to read deposit means error, reset state
            LAST_TRADE_DETAILS = {'asset': None, 'action': None, 'amount': None}
            return

        try:
            # Switch to 'Closed Trades' tab if not active
            closed_tab = driver.find_element(By.CSS_SELECTOR, value='#bar-chart > div > div > div.right-widget-container > div > div.widget-slot__header > div.divider > ul > li:nth-child(2) > a')
            closed_tab_parent = closed_tab.find_element(By.XPATH, value='..')
            was_closed_tab_active = (closed_tab_parent.get_attribute('class') == 'active')
            if not was_closed_tab_active:
                closed_tab.click()
                await asyncio.sleep(0.5) # Wait for tab content to load
        except:
            pass # Deals list not available or element not found

        await set_amount_icon(driver)

        closed_trades = driver.find_elements(By.CLASS_NAME, value='deals-list__item')
        
        trade_result_processed = False

        if closed_trades: # Check only if a closed trade is visible
            
            last_trade = closed_trades[0].text.split('\n')
            
            try:
                # Check win/draw/loss (Indices might vary slightly, using $0 checks)
                is_win = ('$0' != last_trade[4] and '$\u202f0' != last_trade[4])
                is_draw = ('$0' != last_trade[3] and '$\u202f0' != last_trade[3]) and not is_win
                is_loss = not (is_win or is_draw)
                
                
                # --- UNCONDITIONAL MARTINGALE LOGIC ---
                
                # 1. If Win/Draw: Reset Series
                if is_win or is_draw: 
                    log(f"MARTINGALE: Trade result for {asset_name_from_trade}: {'Win' + ' (Draw)' if is_draw else 'Win'} (Step {MARTINGALE_STEP}). Resetting series.")
                    
                    MARTINGALE_STEP = 0
                    MARTINGALE_ACTIVE_ASSET = None
                    MARTINGALE_ACTIVE_ACTION = None
                    MARTINGALE_INITIAL_AMOUNT_SET = False # <--- FIX: Reset amount flag
                    
                    trade_result_processed = True
                    
                # 2. If Loss: Advance Step and Prepare for Execution in Phase 2
                elif is_loss and MARTINGALE_ACTIVE_ASSET is not None:
                    
                    next_step_index = MARTINGALE_STEP + 1
                    
                    if next_step_index < len(MARTINGALE_LIST):
                        # Advance to next step
                        next_amount = MARTINGALE_LIST[next_step_index]
                        
                        # Check safety margin (Optional: check deposit against next amount)
                        if next_amount > current_deposit:
                             log(f'MARTINGALE: Deposit ({current_deposit}) is less than next step ({next_amount}). Resetting series.')
                             MARTINGALE_STEP = 0
                             MARTINGALE_ACTIVE_ASSET = None
                             MARTINGALE_ACTIVE_ACTION = None
                             MARTINGALE_INITIAL_AMOUNT_SET = False # <--- FIX: Reset amount flag
                        else:
                            # Step is safe, advance the step and prepare for execution
                            MARTINGALE_STEP = next_step_index
                            MARTINGALE_LAST_LOSS_TIME[asset_name_from_trade] = datetime.now() # Record loss time for delay
                            log(f"MARTINGALE: Loss detected on {asset_name_from_trade}. Next trade amount: {next_amount} (Step {MARTINGALE_STEP}). Waiting for delay...")

                    else:
                        # End of list reached. Resetting.
                        log(f"MARTINGALE: Loss, but end of list reached (Step {MARTINGALE_STEP}). Resetting series.")
                        MARTINGALE_STEP = 0
                        MARTINGALE_ACTIVE_ASSET = None
                        MARTINGALE_ACTIVE_ACTION = None
                        MARTINGALE_INITIAL_AMOUNT_SET = False # <--- FIX: Reset amount flag

                    trade_result_processed = True
                    
                # 3. Loss on a non-Martingale trade 
                elif is_loss and MARTINGALE_ACTIVE_ASSET is None:
                    # Treat as a regular loss on initial trade, no Martingale active, so just reset trade details
                    log("MARTINGALE: Loss on an unmanaged trade. Resetting trade details.")
                    MARTINGALE_STEP = 0
                    MARTINGALE_INITIAL_AMOUNT_SET = False # <--- FIX: Reset amount flag
                    trade_result_processed = True


                # Clear LAST_TRADE_DETAILS to prevent re-checking the same trade
                if trade_result_processed:
                    LAST_TRADE_DETAILS = {'asset': None, 'action': None, 'amount': None}
                
            except Exception as e:
                log(f"Martingale step update error: {e}")
        
        # Switch back to 'Open Trades' tab if needed
        try:
            open_tab = driver.find_element(By.CSS_SELECTOR, value='#bar-chart > div > div > div.right-widget-container > div > div.widget-slot__header > div.divider > ul > li:nth-child(1) > a')
            open_tab_parent = open_tab.find_element(By.XPATH, value='..')
            if open_tab_parent.get_attribute('class') != 'active':
                open_tab.click()
        except:
            pass
            
    # If Martingale is active (MARTINGALE_ACTIVE_ASSET is not None), flow proceeds to Phase 2 next cycle.


    # --- Phase 2: IMMEDIATE MARTINGALE EXECUTION ---
    
    if SETTINGS.get('MARTINGALE_ENABLED') and MARTINGALE_ACTIVE_ASSET is not None:
        
        asset = MARTINGALE_ACTIVE_ASSET
        action = MARTINGALE_ACTIVE_ACTION
        amount_to_set = MARTINGALE_LIST[MARTINGALE_STEP]
        delay_seconds = SETTINGS.get('MARTINGALE_LOSS_DELAY_SECONDS', 10)
        
        # 1. Check Delay
        last_loss_time = MARTINGALE_LAST_LOSS_TIME.get(asset, datetime.min)
        if last_loss_time + timedelta(seconds=delay_seconds) > datetime.now():
            time_left = (last_loss_time + timedelta(seconds=delay_seconds) - datetime.now()).total_seconds()
            log(f"Immediate Martingale Trade for {asset} delayed. Waiting {time_left:.2f} seconds...")
            return 
            
        log(f"Executing Immediate Martingale Trade: {action.upper()} on {asset} with amount {amount_to_set} (Step {MARTINGALE_STEP}).")

        # 2. Execute Trade
        switch = await switch_to_asset(driver, asset)
        if switch:
            try:
                await set_amount_icon(driver)
                await set_amount_on_ui(driver, amount_to_set) # <--- Sets the Martingale amount (e.g., $3)
                
                # The martingale_override=True flag prevents create_order from resetting the active series state
                # و همچنین از اعمال مجدد منطق VICE_VERSA جلوگیری می کند.
                order_created = await create_order(driver, action, asset, martingale_override=True) 
                
                if order_created:
                    log(f"Immediate Martingale Trade executed successfully. Waiting for result...")
                    await asyncio.sleep(1) 
                    return 

            except Exception as e:
                log(f"Error executing Immediate Martingale Trade: {e}")
                # If execution fails, reset Martingale to prevent permanent lock
                MARTINGALE_STEP = 0
                MARTINGALE_ACTIVE_ASSET = None
                MARTINGALE_ACTIVE_ACTION = None
                MARTINGALE_INITIAL_AMOUNT_SET = False # <--- FIX: Reset amount flag
        else:
            log(f"Could not switch to asset {asset} for immediate Martingale. Resetting series.")
            # If asset switch fails, reset Martingale to prevent permanent lock
            MARTINGALE_STEP = 0 
            MARTINGALE_ACTIVE_ASSET = None
            MARTINGALE_ACTIVE_ACTION = None
            MARTINGALE_INITIAL_AMOUNT_SET = False # <--- FIX: Reset amount flag


    # --- Phase 3: STANDARD STRATEGY EXECUTION ---
    
    # **BLOCK:** Only execute standard trades if no Martingale series is active
    if SETTINGS.get('MARTINGALE_ENABLED') and MARTINGALE_ACTIVE_ASSET is not None:
        # If Martingale is active, wait for Phase 2 to execute or Phase 1 to reset.
        return 

    # **FIX:** Ensure the amount is always set to the initial step amount for a new series
    if SETTINGS.get('MARTINGALE_ENABLED') and not MARTINGALE_INITIAL_AMOUNT_SET: # <--- CHANGED: Only set if the flag is False
        try:
             await set_amount_icon(driver)
             # Set the UI amount to the initial Martingale step (e.g., $1)
             await set_amount_on_ui(driver, MARTINGALE_LIST[0])
             MARTINGALE_INITIAL_AMOUNT_SET = True # <--- CHANGED: Set the flag to True after successful setting
             log(f"Initial Martingale amount set to {MARTINGALE_LIST[0]}.")
        except Exception as e:
             log(f"Error setting initial Martingale amount: {e}")
             return

    action = None
    sstrategy = None
    for asset, candles in CANDLES.items():
        
        # 1. Server Strategies
        if SETTINGS.get('USE_SERVER_STRATEGIES') and \
                asset in SERVER_STRATEGIES and \
                len(SERVER_STRATEGIES[asset]) > 0 and \
                PERIOD == 60:  # TODO: update for timeframe later
            for sstrategy in SERVER_STRATEGIES[asset]:
                action = await check_strategies(candles, sstrategy=sstrategy)
                if action:
                    break # If action found by a server strategy, use it
        
        # 2. Local Strategy (If no server strategy or if not using them)
        if not action:
            action = await check_strategies(candles, sstrategy=None)

        if not action:
            continue
        
        # Execute Order (create_order will initialize Martingale series state upon success)
        # Note: If MARTINGALE_ENABLED is True, the amount is already set to MARTINGALE_LIST[0] ($1) by the block above.
        order_created = await create_order(driver, action, asset, sstrategy=sstrategy)
        if order_created:
            await asyncio.sleep(1) # Small delay after placing an order
            return # Only place one order per check cycle


async def get_candles_yfinance(email, asset, timeframe):
    """Fetches historical candles from a remote server for backtesting."""
    response = requests.get(CANDLES_URL, params={'asset': asset, 'email': email, 'timeframe': timeframe, 'size': 10000})
    if response.status_code != 200:
        raise Exception(response.json()['error'])
    # Format: [['', '', close_value] ...] to fit into strategies where 'close' is index 2
    candles = [['', '', c] for c in response.json()[asset]]
    return candles


async def backtest(email, timeframe='1m'):
    """Runs a backtest on historical data using the current strategy."""
    log(f'--- Starting Backtest on {timeframe} timeframe ---')
    
    # 1. Get Assets
    try:
        assets_response = requests.get(ASSETS_URL, params={'email': email})
        assets_response.raise_for_status()
        assets = assets_response.json()['assets']
    except Exception as e:
        log(f"Error fetching assets for backtest: {e}")
        return

    PROFITS = []
    
    # 2. Backtest each asset
    for asset in assets:
        await asyncio.sleep(0.6) # Gentle delay between requests
        
        try:
            candles = await get_candles_yfinance(email, asset, timeframe=timeframe)
        except Exception as e:
            log(f'Backtest on {asset}: No candles available or error fetching ({e}). Skipping.')
            continue
            
        if not candles:
            log(f'Backtest on {asset}: No candles available. Skipping.')
            continue

        # Determine minimum required candles for the slowest indicator
        size = max(SETTINGS['SLOW_MA'], SETTINGS['RSI_PERIOD']) + 11
        if len(candles) < size + 3:
            log(f'Backtest on {asset}: Not enough historical data. Need at least {size+3} candles.')
            continue

        actions = {}
        # Simulate strategy execution candle by candle
        for i in range(size, len(candles) - 3): # -3 to ensure we have enough lookahead for max estimation
            candles_part = candles[i-size:i+1] # The current 'view' of candles
            
            # The last candle in candles_part is the candle *closing* at time i
            action = await check_strategies(candles_part) 
            
            if action:
                # Apply Vice-Versa before recording the trade direction
                if SETTINGS['VICE_VERSA']:
                    action = 'call' if action == 'put' else 'put'
                actions[i] = action # Record the trade taken at the close of candle i
                
        try:
            per = int(len(candles) / len(actions))
        except ZeroDivisionError:
            per = 0
            
        log(f'Backtest on {asset} with {timeframe} timeframe! Total trades: {len(actions)}. Frequency: 1 order per {per} candles. ')
        
        # 3. Check results for different estimations (trade expiration in candles)
        for estimation in [1, 2, 3]:  # candles (e.g., 1 candle expiration, 2 candle expiration)
            wins = 0
            draws = 0
            total_trades = 0
            
            for trade_index, action in actions.items():
                
                target_index = trade_index + estimation # The candle index when the trade expires
                
                if target_index >= len(candles):
                    continue # Skip if out of bounds (not enough future data)
                    
                total_trades += 1
                
                # candles[trade_index][2] is the closing price of the candle that triggered the trade (i.e., open price)
                # candles[target_index][2] is the closing price of the candle at expiration time
                
                trigger_price = candles[trade_index][2]
                expiration_price = candles[target_index][2]
                
                if trigger_price == expiration_price:
                    draws += 1
                elif action == 'call' and trigger_price < expiration_price:
                    wins += 1
                elif action == 'put' and trigger_price > expiration_price:
                    wins += 1

            try:
                # Profit calculation: Wins / (Total Trades - Draws)
                denominator = total_trades - draws
                profit = (wins * 100) // denominator if denominator > 0 else 0
                PROFITS.append(profit)
                log(f'  By estimation of {estimation} candles: Wins: {wins}, Draws: {draws}. Profit: {profit}%')
            except ZeroDivisionError:
                log(f'  By estimation of {estimation} candles: No decisive trades.')
                continue

    # 4. Final Summary
    if PROFITS:
        average_profit = sum(PROFITS) // len(PROFITS)
        log(f'Backtest average profit for all assets: {average_profit}%')
    else:
        log('No successful backtest results.')
        
    log('--- Backtest ended, trading... ---')


async def main():
    """The main entry point for the trading bot."""
    
    log("Starting trading bot...")
    
    # 1. Read Settings
    read_settings()
    
    # 2. Configure Environment
    await set_remote_debugging_allowed()

    # 3. Initialize WebDriver
    try:
        driver = await get_driver()
        driver.get(URL)
        await asyncio.sleep(5) # Give time for the page to load and login if needed
        log("Browser launched. Please login if necessary.")
    except Exception as e:
        log(f"Failed to initialize WebDriver: {e}")
        return

    # 4. Main Trading Loop
    try:
        while True:
            # Check deposit/profit/loss limits
            await check_deposit(driver)
            
            if TRADING_ALLOWED:
                # Process incoming data (candles, assets)
                await websocket_log(driver)
                
                # Check for trade signals and execute orders
                await check_indicators(driver)
            else:
                log("Trading is currently stopped due to Stop Loss or Take Profit limits.")

            # Main loop delay (adjust as needed, but short delays are fine if logic is fast)
            await asyncio.sleep(0.5)

    except KeyboardInterrupt:
        log("Bot stopped by user.")
    except Exception as e:
        log(f"An unexpected error occurred in the main loop: {e}")
    finally:
        driver.quit()
        log("Browser closed. Exiting.")


if __name__ == '__main__':
    # Ensure settings.txt exists with at least default values for first run
    if not os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, 'w') as f:
                f.write("# Trading Bot Settings (Set to True/False or the specified value)\n")
                f.write("FAST_MA=5\n")
                f.write("SLOW_MA=20\n")
                f.write("FAST_MA_TYPE=SMA\n")
                f.write("SLOW_MA_TYPE=SMA\n")
                f.write("RSI_ENABLED=False\n")
                f.write("RSI_PERIOD=14\n")
                f.write("RSI_UPPER=70\n")
                f.write("RSI_CALL_SIGN=>\n")
                f.write("COUNT_BULLISH=0\n")
                f.write("COUNT_BEARISH=0\n")
                f.write("MARTINGALE_ENABLED=True\n")
                f.write("MARTINGALE_LOSS_DELAY_SECONDS=10\n")
                f.write("MARTINGALE_LIST=1,3,10,18,39,80\n")
                f.write("TAKE_PROFIT_ENABLED=False\n")
                f.write("TAKE_PROFIT=100\n")
                f.write("STOP_LOSS_ENABLED=False\n")
                f.write("STOP_LOSS=50\n")
                f.write("VICE_VERSA=False\n")
                f.write("BEGINNING_CANDLE_ORDER=False\n")
                f.write("USE_SERVER_STRATEGIES=False\n")
                f.write("BACKTEST=False\n")
                f.write("BACKTEST_TIMEFRAME=1m\n")
                f.write("MIN_PAYOUT=70\n")
            log(f"Created default settings file: {SETTINGS_PATH}. Please review and configure.")
        except:
             log(f"Could not create settings file: {SETTINGS_PATH}. Using defaults.")

    # Run the main asynchronous function
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Program interrupted and closed.")
    except RuntimeError as e:
        if "cannot run" in str(e).lower() and "running event loop" in str(e).lower():
            log("Running asyncio.run in an already running event loop. This usually happens in interactive environments like Jupyter or some IDEs. Exiting.")
        else:
            raise