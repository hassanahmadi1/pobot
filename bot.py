import asyncio
import base64
import json
import operator
import os
import platform
import random
import sys
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

import requests
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc


# --- Operators ---
OPS = {
    '>': operator.gt,
    '<': operator.lt,
}

# --- Global Configuration ---
# NOTE: Pocket Option URLs may change. Using the main domain with affiliate params.
URL = 'https://pocketoption.com/cabinet/demo-quick-high-low'
BASE_URL = 'https://policensor.com'
LICENSE_URL = f'{BASE_URL}/validate_payment/'
ASSETS_URL = f'{BASE_URL}/assets/'
CANDLES_URL = f'{BASE_URL}/close_candles/'
LIMIT_TRADES_URL = f'{BASE_URL}/limit_trades/'
SERVER_STRATEGIES_URL = f'{BASE_URL}/server_strategies/'
PRODUCT_ID = 'prod_RWzyaFqdRawZim'
LICENSE_BUY_URL = 'https://buy.stripe.com/3cs9Dw3W8dMT2u44gg?prefilled_email='
PERIOD = 60  # default is 60 seconds (1 minute)

# --- Global State ---
ASSETS: Dict = {}
CANDLES: Dict = {}
ACTIONS: Dict = {}
LICENSE_VALID: Optional[bool] = None
TRADES = 0
TRADING_ALLOWED = True
CURRENT_ASSET: Optional[str] = None
FAVORITES_REANIMATED = False
SETTINGS: Dict = {}
MARTINGALE_LIST: List[int] = []
MARTINGALE_LAST_ACTION_ENDS_AT = datetime.now()
MARTINGALE_INITIAL = True
NUMBERS = {
    '0': '11', '1': '7', '2': '8', '3': '9', '4': '4',
    '5': '5', '6': '6', '7': '1', '8': '2', '9': '3',
}
INITIAL_DEPOSIT: Optional[float] = None
SETTINGS_PATH = 'settings.txt'
SERVER_STRATEGIES: Dict = {}

# Martingale State Tracking
LAST_TRADE_DETAILS = {'asset': None, 'action': None, 'amount': None}
MARTINGALE_ACTIVE_ASSET: Optional[str] = None
MARTINGALE_ACTIVE_ACTION: Optional[str] = None
MARTINGALE_STEP = 0
MARTINGALE_LAST_LOSS_TIME: Dict = {}
MARTINGALE_INITIAL_AMOUNT_SET = False


def log(*args) -> None:
    """Logs messages with a timestamp."""
    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *args, flush=True)


# --- Utility Functions ---

async def set_remote_debugging_allowed() -> None:
    """Sets RemoteDebuggingAllowed in Windows Registry for Chrome (if on Windows)."""
    os_platform = platform.platform().lower()
    if 'windows' not in os_platform:
        return
    try:
        import winreg
        key_path = r"SOFTWARE\Policies\Google\Chrome"
        value_name = "RemoteDebuggingAllowed"
        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            key_path,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY
        )
        current_value, _ = winreg.QueryValueEx(key, value_name)
        if current_value != 1:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, 1)
            log("Set RemoteDebuggingAllowed to 1 in regedit")
        winreg.CloseKey(key)
    except Exception:
        pass


async def get_driver() -> uc.Chrome:
    """
    Initializes and returns an undetected_chromedriver instance.
    Updated for undetected-chromedriver v3.x and Selenium 4.x.
    """
    options = uc.ChromeOptions()
    
    # Performance logging for WebSocket capture
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    
    # Standard stealth options
    options.add_argument('--ignore-ssl-errors')
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--ignore-certificate-errors-spki-list')
    options.add_argument('--disable-build-check')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    
    # User data directory for persistence
    username = os.environ.get('USER', os.environ.get('USERNAME', 'user'))
    os_platform = platform.platform().lower()
    
    if 'macos' in os_platform or 'darwin' in os_platform:
        path_default = fr'/Users/{username}/Library/Application Support/Google/Chrome/Trading Bot Profile'
    elif 'windows' in os_platform:
        path_default = fr'C:\Users\{username}\AppData\Local\Google\Chrome\User Data\Trading Bot Profile'
    elif 'linux' in os_platform:
        path_default = os.path.expanduser('~/.config/google-chrome/Trading Bot Profile')
    else:
        path_default = os.path.expanduser('~/.config/google-chrome/Trading Bot Profile')
    
    options.add_argument(f'--user-data-dir={path_default}')
    
    # Initialize driver with version detection
    # version_main=0 means auto-detect
    driver = uc.Chrome(
        options=options,
        version_main=0,  # Auto-detect Chrome version
        headless=False,
        enable_cdp_events=True,  # Enable CDP for better log capture
        suppress_welcome=True,
        use_subprocess=True,
    )
    
    # Set window size for consistent element positioning
    driver.set_window_size(1366, 768)
    
    # Execute CDP command to enable Network and Log domains
    try:
        driver.execute_cdp_cmd('Network.enable', {})
        driver.execute_cdp_cmd('Log.enable', {})
    except Exception as e:
        log(f"CDP enable warning: {e}")
    
    return driver


async def get_email(driver: uc.Chrome) -> Optional[str]:
    """Retrieves the user's email from the trade platform interface."""
    try:
        # Try multiple selectors for email
        selectors = [
            (By.CLASS_NAME, 'info__email'),
            (By.CSS_SELECTOR, '.info__email div[data-hd-show]'),
            (By.CSS_SELECTOR, '[data-hd-show*="@"]'),
        ]
        
        for by, selector in selectors:
            try:
                elements = driver.find_elements(by, selector)
                for elem in elements:
                    email = elem.get_attribute('data-hd-show') or elem.text
                    if '@' in email:
                        return email.strip()
            except Exception:
                continue
        return None
    except Exception:
        return None


async def hand_delay() -> None:
    """Introduces a small, random delay to simulate human interaction."""
    await asyncio.sleep(random.uniform(0.2, 0.6))


def cleanup_martingale_list(value: str) -> List[int]:
    """Validates and cleans the Martingale list string."""
    value = value.replace(' ', '')
    value_list = value.split(',')
    try:
        value_list = [int(v) for v in value_list]
    except ValueError:
        raise ValueError("Martingale list must contain only integers.")
    
    if len(value_list) < 2 or value_list[0] < 1 or value_list[-1] > 20000:
        raise ValueError("Invalid Martingale list format or values.")
    
    martingale_list = []
    for i, v in enumerate(value_list):
        if i == 0:
            martingale_list.append(v)
        elif value_list[i-1] < value_list[i]:
            martingale_list.append(v)
        else:
            raise ValueError("Martingale amounts must be strictly increasing.")
    return martingale_list


def read_settings() -> None:
    """Reads settings from the settings.txt file or sets defaults."""
    global SETTINGS, MARTINGALE_LIST
    
    # Default settings
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
        'MARTINGALE_LOSS_DELAY_SECONDS': 10,
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
    
    SETTINGS.update(default_settings)
    log("Default settings loaded.")
    
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                try:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    if key in ['FAST_MA', 'SLOW_MA', 'RSI_PERIOD', 'RSI_UPPER', 
                               'TAKE_PROFIT', 'STOP_LOSS', 'MIN_PAYOUT', 
                               'COUNT_BULLISH', 'COUNT_BEARISH', 
                               'MARTINGALE_LOSS_DELAY_SECONDS']:
                        SETTINGS[key] = int(value)
                    elif key in ['RSI_CALL_SIGN']:
                        if value in OPS:
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
                    elif key in ['RSI_ENABLED', 'MARTINGALE_ENABLED', 'TAKE_PROFIT_ENABLED', 
                                 'STOP_LOSS_ENABLED', 'VICE_VERSA', 'BEGINNING_CANDLE_ORDER', 
                                 'USE_SERVER_STRATEGIES', 'BACKTEST']:
                        SETTINGS[key] = value.lower() in ('true', '1', 't', 'y', 'yes')
                    elif key in ['BACKTEST_TIMEFRAME']:
                        SETTINGS[key] = value
                    elif key not in ['MARTINGALE_ADVANCED_ENABLED']:
                        log(f"Unknown setting at line {line_num}: {key}")
                except Exception as e:
                    log(f"Error parsing setting line {line_num} '{line}': {e}. Using default.")
        
        log("Settings file loaded and parsed successfully.")
        
        # Final validation
        if isinstance(SETTINGS['MARTINGALE_LIST'], str):
            SETTINGS['MARTINGALE_LIST'] = cleanup_martingale_list(SETTINGS['MARTINGALE_LIST'])
        
        MARTINGALE_LIST = SETTINGS['MARTINGALE_LIST']
        
    except FileNotFoundError:
        log(f"Settings file '{SETTINGS_PATH}' not found. Using default settings.")
    except Exception as e:
        log(f"Unexpected error reading settings: {e}. Using default settings.")


# --- Candle Processing ---

async def websocket_log(driver: uc.Chrome) -> None:
    """Processes WebSocket log data to update candles and state."""
    global ASSETS, PERIOD, CANDLES, ACTIONS, LICENSE_VALID, TRADES, CURRENT_ASSET, FAVORITES_REANIMATED, TRADING_ALLOWED, SERVER_STRATEGIES
    
    try:
        logs = driver.get_log('performance')
    except Exception as e:
        log(f"Error getting performance logs: {e}")
        return
    
    for wsData in logs:
        try:
            message = json.loads(wsData['message'])['message']
            response = message.get('params', {}).get('response', {})
            
            if response.get('opcode', 0) == 2:
                try:
                    payload_str = base64.b64decode(response['payloadData']).decode('utf-8')
                    data = json.loads(payload_str)
                except Exception:
                    continue
                
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
                        candle[2] = value
                        if value > candle[3]:
                            candle[3] = value
                        if value < candle[4]:
                            candle[4] = value
                        if tstamp % PERIOD == 0:
                            if tstamp not in [c[0] for c in candles]:
                                candles.append([tstamp, value, value, value, value])
                    CANDLES[data['asset']] = candles
                
                # Process real-time updates
                try:
                    asset = data[0][0]
                    candles = CANDLES.get(asset, [])
                    if candles:
                        current_value = data[0][2]
                        candles[-1][2] = current_value
                        if current_value > candles[-1][3]:
                            candles[-1][3] = current_value
                        if current_value < candles[-1][4]:
                            candles[-1][4] = current_value
                        tstamp = int(float(data[0][1]))
                        if tstamp % PERIOD == 0:
                            if tstamp not in [c[0] for c in candles]:
                                candles.append([tstamp, current_value, current_value, current_value, current_value])
                except Exception:
                    pass
        except Exception:
            continue
    
    if not FAVORITES_REANIMATED:
        try:
            await reanimate_favorites(driver)
        except Exception:
            pass
    
    # License check bypass (assume valid for unlimited trades)
    if LICENSE_VALID is None:
        try:
            LICENSE_VALID = True
            log("License check bypassed. Assuming valid license for unlimited trades.")
            
            if SETTINGS.get('BACKTEST'):
                email = await get_email(driver)
                if email:
                    await backtest(email, timeframe=SETTINGS['BACKTEST_TIMEFRAME'][:-1])
                else:
                    log("Could not get email to start backtest.")
        except Exception as e:
            log(f"License check error: {e}")
    
    if SETTINGS.get('USE_SERVER_STRATEGIES') and not SERVER_STRATEGIES:
        try:
            response = requests.get(SERVER_STRATEGIES_URL, timeout=10)
            if response.status_code == 200:
                SERVER_STRATEGIES = response.json()
                log('Server strategies downloaded')
        except Exception as e:
            log(f"Error fetching server strategies: {e}")


async def reanimate_favorites(driver: uc.Chrome) -> None:
    """Clicks on each favorite asset to ensure the bot is getting their data."""
    global CURRENT_ASSET, FAVORITES_REANIMATED
    
    try:
        asset_favorites_items = driver.find_elements(By.CLASS_NAME, 'assets-favorites-item')
        for item in asset_favorites_items:
            try:
                item_class = item.get_attribute('class') or ''
                if 'assets-favorites-item--active' in item_class:
                    CURRENT_ASSET = item.get_attribute('data-id')
                    continue
                if 'assets-favorites-item--not-active' in item_class:
                    continue
                item.click()
                await hand_delay()
                FAVORITES_REANIMATED = True
            except ElementNotInteractableException:
                log(f"Asset {item.get_attribute('data-id')} is out of reach. Please close some favorite assets.")
                break
            except StaleElementReferenceException:
                continue
    except Exception as e:
        log(f"Reanimate favorites error: {e}")


async def switch_to_asset(driver: uc.Chrome, asset: str) -> bool:
    """Switches the chart to the specified asset."""
    global CURRENT_ASSET
    
    try:
        asset_favorites_items = driver.find_elements(By.CLASS_NAME, 'assets-favorites-item')
        for item in asset_favorites_items:
            if item.get_attribute('data-id') != asset:
                continue
            for _ in range(10):
                await asyncio.sleep(0.1)
                item_class = item.get_attribute('class') or ''
                if 'assets-favorites-item--active' in item_class:
                    CURRENT_ASSET = asset
                    return True
                try:
                    item.click()
                    await hand_delay()
                except Exception:
                    pass
        
        if asset == CURRENT_ASSET:
            return True
        return False
    except Exception as e:
        log(f"Switch to asset error: {e}")
        return False


async def check_payout(driver: uc.Chrome, asset: str) -> bool:
    """Checks if the current asset payout meets the minimum requirement."""
    global ACTIONS
    
    try:
        # Try multiple selectors for payout
        selectors = [
            (By.CLASS_NAME, 'value__val-start'),
            (By.CSS_SELECTOR, '.value__val-start'),
            (By.CSS_SELECTOR, '[class*="payout"]'),
            (By.CSS_SELECTOR, '.bet-profit-value'),
        ]
        
        payout_text = None
        for by, selector in selectors:
            try:
                elem = driver.find_element(by, selector)
                payout_text = elem.text
                break
            except NoSuchElementException:
                continue
        
        if not payout_text:
            log(f"Payout element not found for asset {asset}")
            return False
        
        # Parse payout (e.g., "$80%" or "80%")
        payout_clean = payout_text.replace('$', '').replace('%', '').replace('\u202f', '').strip()
        payout = int(float(payout_clean))
        
        if payout >= SETTINGS['MIN_PAYOUT']:
            return True
        
        log(f'Payout {payout}% is not allowed for asset {asset} (Min: {SETTINGS["MIN_PAYOUT"]}%).')
        ACTIONS[asset] = datetime.now() + timedelta(minutes=1)
        return False
    except Exception as e:
        log(f"Could not read payout for asset {asset}: {e}")
        return False


async def check_trades() -> bool:
    """Bypassed: Always returns True to allow unlimited trades."""
    return True


# --- Trading Logic ---

async def set_amount_icon(driver: uc.Chrome) -> None:
    """Switches the trading amount input to be in currency (USD) instead of percentage."""
    try:
        # Try multiple selectors for the currency switch
        selectors = [
            '#put-call-buttons-chart-1 > div > div.blocks-wrap > div.block.block--bet-amount > div.block__control.control > div.control-buttons__wrapper > div > a',
            '.block--bet-amount .control-buttons__wrapper a',
            '[class*="bet-amount"] [class*="currency-icon"]',
        ]
        
        for selector in selectors:
            try:
                amount_style = driver.find_element(By.CSS_SELECTOR, selector)
                # Check if USD icon is already active
                try:
                    amount_style.find_element(By.CLASS_NAME, 'currency-icon--usd')
                    return  # Already in USD mode
                except NoSuchElementException:
                    amount_style.click()
                    await hand_delay()
                    return
            except NoSuchElementException:
                continue
    except Exception as e:
        log(f"Set amount icon error: {e}")


async def set_amount_on_ui(driver: uc.Chrome, amount: int) -> None:
    """Sets the trade amount on the UI using the virtual keyboard."""
    base = '#modal-root > div > div > div > div > div.trading-panel-modal__in > div.virtual-keyboard > div > div:nth-child(%s) > div'
    
    try:
        # Find and click the amount input
        amount_input_selectors = [
            '#put-call-buttons-chart-1 > div > div.blocks-wrap > div.block.block--bet-amount > div.block__control.control > div.control__value.value.value--several-items > div > input[type=text]',
            '.block--bet-amount input[type="text"]',
            '[class*="bet-amount"] input',
        ]
        
        amount_element = None
        for selector in amount_input_selectors:
            try:
                amount_element = driver.find_element(By.CSS_SELECTOR, selector)
                break
            except NoSuchElementException:
                continue
        
        if not amount_element:
            log("Amount input element not found")
            return
        
        amount_element.click()
        await hand_delay()
        
        # Clear input using backspace or clear button
        try:
            clear_button = driver.find_element(By.CSS_SELECTOR, base % '12')
            for _ in range(5):
                clear_button.click()
                await hand_delay()
        except Exception:
            # Fallback: use keyboard clear
            amount_element.clear()
            await hand_delay()
        
        # Enter new amount
        for number in str(amount):
            try:
                key_selector = base % NUMBERS[number]
                driver.find_element(By.CSS_SELECTOR, key_selector).click()
                await hand_delay()
            except Exception as e:
                log(f"Error entering digit {number}: {e}")
                
    except Exception as e:
        log(f"Set amount on UI error: {e}")


async def set_estimation_icon(driver: uc.Chrome) -> None:
    """Switches the expiration time style if needed."""
    try:
        time_style = driver.find_element(By.CSS_SELECTOR, 
            '#put-call-buttons-chart-1 > div > div.blocks-wrap > div.block.block--expiration-inputs > div.block__control.control > div.control-buttons__wrapper > div > a > div > div > svg')
        if 'exp-mode-2.svg' in time_style.get_attribute('data-src') or 'exp-mode-2' in time_style.get_attribute('src'):
            time_style.click()
            await hand_delay()
    except Exception as e:
        log(f"Set estimation icon error: {e}")


async def get_estimation(driver: uc.Chrome) -> int:
    """Gets the current trade expiration time in seconds."""
    try:
        estimation = driver.find_element(By.CSS_SELECTOR,
            '#put-call-buttons-chart-1 > div > div.blocks-wrap > div.block.block--expiration-inputs > div.block__control.control > div.control__value.value.value--several-items')
        est = datetime.strptime(estimation.text, '%H:%M:%S')
        return (est.hour * 3600) + (est.minute * 60) + est.second
    except Exception as e:
        log(f"Get estimation error: {e}")
        return 60  # Default to 1 minute


async def create_order(driver: uc.Chrome, action: str, asset: str, 
                        sstrategy: Optional[Dict] = None, 
                        martingale_override: bool = False) -> bool:
    """
    Executes a trade order (Call/Put).
    martingale_override: True if this is a Martingale step trade.
    """
    global ACTIONS, MARTINGALE_LAST_ACTION_ENDS_AT, LAST_TRADE_DETAILS, \
           MARTINGALE_ACTIVE_ASSET, MARTINGALE_ACTIVE_ACTION, MARTINGALE_STEP, MARTINGALE_INITIAL_AMOUNT_SET
    
    # Check for trade delay
    if ACTIONS.get(asset) and ACTIONS[asset] + timedelta(seconds=PERIOD * 2) > datetime.now():
        return False
    
    # Check for asset lock (Only applies to standard trades)
    if not martingale_override and SETTINGS.get('MARTINGALE_ENABLED') and MARTINGALE_ACTIVE_ASSET is not None:
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
        
        # 3. Check Trade Limit
        if not await check_trades():
            return False
        
        # 4. Determine final action
        if martingale_override:
            current_action = action
        else:
            vice_versa = sstrategy['vice_versa'] if sstrategy else SETTINGS['VICE_VERSA']
            current_action = 'call' if action == 'put' else 'put' if vice_versa else action
        
        # 5. Get and Record Amount
        await set_amount_icon(driver)
        try:
            amount_element = driver.find_element(By.CSS_SELECTOR,
                '#put-call-buttons-chart-1 > div > div.blocks-wrap > div.block.block--bet-amount > div.block__control.control > div.control__value.value.value--several-items > div > input[type=text]')
            amount_value = int(float(amount_element.get_attribute('value').replace(',', '').replace('$', '').replace('\u202f', '')))
        except Exception:
            amount_value = MARTINGALE_LIST[0] if MARTINGALE_LIST else 1
        
        # 6. Execute Order
        btn_class = f'btn-{current_action}'
        try:
            driver.find_element(By.CLASS_NAME, btn_class).click()
        except NoSuchElementException:
            # Try alternative selectors
            for selector in [f'.btn-{current_action}', f'[class*="btn-{current_action}"]', f'button.{btn_class}']:
                try:
                    driver.find_element(By.CSS_SELECTOR, selector).click()
                    break
                except NoSuchElementException:
                    continue
            else:
                log(f"Could not find {current_action} button")
                return False
        
        ACTIONS[asset] = datetime.now()
        
        message = f'{current_action.capitalize()} on asset: {asset} with amount {amount_value}'
        if sstrategy:
            message += f' made by server strategy with profit {sstrategy.get("profit", "?")}%'
        log(message)
        
        # 7. Martingale Logic on Order Success
        if SETTINGS.get('MARTINGALE_ENABLED'):
            LAST_TRADE_DETAILS = {
                'asset': asset,
                'action': current_action,
                'amount': amount_value
            }
            
            if not martingale_override and MARTINGALE_ACTIVE_ASSET is None:
                MARTINGALE_ACTIVE_ASSET = asset
                MARTINGALE_ACTIVE_ACTION = current_action
                MARTINGALE_STEP = 0
                MARTINGALE_INITIAL_AMOUNT_SET = True
            
            await set_estimation_icon(driver)
            seconds = await get_estimation(driver)
            MARTINGALE_LAST_ACTION_ENDS_AT = datetime.now() + timedelta(seconds=seconds)
    
    except Exception as e:
        log(f"Can't create order: {e}")
        return False
    
    return True


# --- Indicators and Strategies ---

async def calculate_last_wma(candles: List[float], period: int) -> float:
    """Weighted Moving Average calculation."""
    if len(candles) < period:
        return sum(candles) / len(candles) if candles else 0
    weights = list(range(1, period + 1))
    weighted_prices = [candles[i] * weights[i] for i in range(-period, 0)]
    return sum(weighted_prices) / sum(weights)


async def calculate_last_ema(candles: List[float], period: int, multiplier: float) -> float:
    """Exponential Moving Average calculation."""
    if len(candles) < period + 1:
        return sum(candles) / len(candles) if candles else 0
    
    sma = sum(candles[:period]) / period
    ema = sma
    
    for price in candles[period:]:
        ema = (price - ema) * multiplier + ema
    
    return ema


async def moving_averages_cross(candles: List[List], sstrategy: Optional[Dict] = None) -> Optional[str]:
    """Checks for a Moving Average crossover signal."""
    fast_ma = sstrategy['fast_ma'] if sstrategy else SETTINGS['FAST_MA']
    fast_ma_type = sstrategy['fast_ma_type'] if sstrategy else SETTINGS.get('FAST_MA_TYPE', 'SMA')
    slow_ma = sstrategy['slow_ma'] if sstrategy else SETTINGS['SLOW_MA']
    slow_ma_type = sstrategy['slow_ma_type'] if sstrategy else SETTINGS.get('SLOW_MA_TYPE', 'SMA')
    
    close_prices = [c[2] for c in candles]
    
    if fast_ma >= slow_ma:
        log("Moving averages 'fast' can't be bigger than or equal to 'slow'")
        return None
    
    if len(close_prices) < slow_ma + 10:
        return None
    
    async def get_ma(prices: List[float], period: int, ma_type: str, slice_end_offset: int) -> float:
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
    
    # Calculate Current MA
    fast_ma_current = await get_ma(close_prices, fast_ma, fast_ma_type, -1)
    slow_ma_current = await get_ma(close_prices, slow_ma, slow_ma_type, -1)
    
    try:
        if fast_ma_previous < slow_ma_previous and fast_ma_current > slow_ma_current:
            return 'call'
        elif fast_ma_previous > slow_ma_previous and fast_ma_current < slow_ma_current:
            return 'put'
    except Exception as e:
        log(f"MA cross error: {e}")
    
    return None


async def get_rsi(candles: List[List], sstrategy: Optional[Dict] = None) -> List[Optional[float]]:
    """Calculates the Relative Strength Index (RSI)."""
    period = sstrategy['rsi_period'] if sstrategy else SETTINGS['RSI_PERIOD']
    close_prices = [c[2] for c in candles]
    
    if len(close_prices) < period + 1:
        raise ValueError("Not enough data to calculate RSI.")
    
    gains = []
    losses = []
    
    for i in range(1, period + 1):
        delta = close_prices[i] - close_prices[i - 1]
        if delta > 0:
            gains.append(delta)
        else:
            losses.append(abs(delta))
    
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    
    rsi_values = [None] * period
    
    if avg_loss == 0:
        rsi_values.append(100)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100 - (100 / (1 + rs)))
    
    for i in range(period + 1, len(close_prices)):
        delta = close_prices[i] - close_prices[i - 1]
        gain = max(delta, 0)
        loss = abs(min(delta, 0))
        
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        rsi_values.append(rsi)
    
    return rsi_values


def get_rsi_lower(rsi_upper: int) -> int:
    """Calculates the corresponding RSI lower boundary."""
    return 100 - rsi_upper


def get_rsi_put_sign(call_sign: str) -> str:
    """Determines the appropriate sign for a 'put' action based on the 'call' sign."""
    return '<' if call_sign == '>' else '>'


async def rsi_strategy(candles: List[List], action: str, sstrategy: Optional[Dict] = None) -> Optional[str]:
    """Applies the RSI filter based on overbought/oversold levels."""
    try:
        rsi_values = await get_rsi(candles, sstrategy)
    except ValueError:
        return None
    
    rsi_upper = sstrategy['rsi_upper'] if sstrategy else SETTINGS.get('RSI_UPPER', 70)
    rsi_lower = get_rsi_lower(rsi_upper)
    call_sign = sstrategy['rsi_call_sign'] if sstrategy else SETTINGS.get('RSI_CALL_SIGN', '>')
    put_sign = get_rsi_put_sign(call_sign)
    
    current_rsi = rsi_values[-1]
    if current_rsi is None:
        return None
    
    if action == 'call' and OPS[call_sign](current_rsi, rsi_upper):
        return 'call'
    elif action == 'put' and OPS[put_sign](current_rsi, rsi_lower):
        return 'put'
    
    return None


async def check_consecutive_candles(candles: List[List], action: str, sstrategy: Optional[Dict] = None) -> Optional[str]:
    """Checks for consecutive bullish/bearish candles."""
    count_bullish = 0
    count_bearish = 0
    
    if sstrategy:
        count_bullish = sstrategy.get('count_bullish', 0)
        count_bearish = sstrategy.get('count_bearish', 0)
    else:
        count_bullish = SETTINGS.get('COUNT_BULLISH', 0)
        count_bearish = SETTINGS.get('COUNT_BEARISH', 0)
    
    required = max(count_bullish, count_bearish)
    if len(candles) < required + 1:
        return None
    
    # Skip the current (partially formed) candle - use candles[:-1]
    closed_candles = candles[:-1]
    
    if action == 'call' and count_bullish > 0:
        consecutive = 0
        for candle in reversed(closed_candles):
            if candle[2] > candle[1]:  # close > open
                consecutive += 1
            else:
                break
            if consecutive >= count_bullish:
                return 'call'
        return None
    
    elif action == 'put' and count_bearish > 0:
        consecutive = 0
        for candle in reversed(closed_candles):
            if candle[2] < candle[1]:  # close < open
                consecutive += 1
            else:
                break
            if consecutive >= count_bearish:
                return 'put'
        return None
    
    return action


async def check_strategies(candles: List[List], sstrategy: Optional[Dict] = None) -> Optional[str]:
    """Aggregates all active strategies."""
    min_required = SETTINGS['SLOW_MA'] + 10
    if sstrategy and 'slow_ma' in sstrategy:
        min_required = max(min_required, sstrategy['slow_ma'] + 10)
    if sstrategy and 'rsi_period' in sstrategy:
        min_required = max(min_required, sstrategy['rsi_period'] + 10)
    
    if len(candles) < min_required:
        return None
    
    # 1. Moving Averages Cross (Primary Signal)
    action = await moving_averages_cross(candles, sstrategy=sstrategy)
    if not action:
        return None
    
    # 2. RSI Filter
    rsi_enabled = False
    if sstrategy and 'rsi_period' in sstrategy:
        rsi_enabled = True
    elif SETTINGS.get('RSI_ENABLED'):
        rsi_enabled = True
    
    if rsi_enabled:
        action = await rsi_strategy(candles, action, sstrategy=sstrategy)
        if not action:
            return None
    
    # 3. Consecutive Candle Count Filter
    has_count_filter = False
    if sstrategy and ('count_bullish' in sstrategy or 'count_bearish' in sstrategy):
        has_count_filter = True
    elif SETTINGS.get('COUNT_BULLISH', 0) > 0 or SETTINGS.get('COUNT_BEARISH', 0) > 0:
        has_count_filter = True
    
    if has_count_filter:
        action = await check_consecutive_candles(candles, action, sstrategy=sstrategy)
        if not action:
            return None
    
    return action


async def check_deposit(driver: uc.Chrome) -> None:
    """Monitors deposit for Stop Loss and Take Profit."""
    global INITIAL_DEPOSIT, TRADING_ALLOWED
    
    try:
        deposit_selectors = [
            'body > div.wrapper > div.wrapper__top > header > div.right-block.js-right-block > div.right-block__item.js-drop-down-modal-open > div > div.balance-info-block__data > div.balance-info-block__balance > span',
            '.balance-info-block__balance span',
            '[class*="balance"] span',
            '.js-balance-value',
        ]
        
        deposit_element = None
        for selector in deposit_selectors:
            try:
                deposit_element = driver.find_element(By.CSS_SELECTOR, selector)
                break
            except NoSuchElementException:
                continue
        
        if not deposit_element:
            return
        
        deposit_text = deposit_element.text.replace(',', '').replace('$', '').replace('\u202f', '').strip()
        deposit = float(deposit_text)
    except Exception as e:
        log(f"Error reading deposit: {e}")
        return
    
    if INITIAL_DEPOSIT is None:
        INITIAL_DEPOSIT = deposit
        log(f'Initial deposit: {INITIAL_DEPOSIT}')
        await asyncio.sleep(1)
        return
    
    if SETTINGS.get('TAKE_PROFIT_ENABLED'):
        take_profit_level = INITIAL_DEPOSIT + SETTINGS.get('TAKE_PROFIT', 100)
        if deposit >= take_profit_level:
            log(f'Take profit reached ({take_profit_level:.2f}), trading stopped. Current: {deposit:.2f}')
            TRADING_ALLOWED = False
    
    if SETTINGS.get('STOP_LOSS_ENABLED'):
        stop_loss_level = INITIAL_DEPOSIT - SETTINGS.get('STOP_LOSS', 50)
        if deposit <= stop_loss_level:
            log(f'Stop loss reached ({stop_loss_level:.2f}), trading stopped. Current: {deposit:.2f}')
            TRADING_ALLOWED = False


async def check_indicators(driver: uc.Chrome) -> None:
    """Main loop for checking indicators and placing trades."""
    global MARTINGALE_LAST_ACTION_ENDS_AT, \
           LAST_TRADE_DETAILS, MARTINGALE_ACTIVE_ASSET, MARTINGALE_ACTIVE_ACTION, MARTINGALE_STEP, \
           MARTINGALE_LAST_LOSS_TIME, MARTINGALE_LIST, MARTINGALE_INITIAL_AMOUNT_SET
    
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
        
        # Wait for trade to expire + buffer
        if MARTINGALE_LAST_ACTION_ENDS_AT + timedelta(seconds=4) > datetime.now():
            return
        
        log(f"MARTINGALE: Checking result for last trade on {asset_name_from_trade}...")
        
        try:
            deposit_selectors = [
                'body > div.wrapper > div.wrapper__top > header > div.right-block.js-right-block > div.right-block__item.js-drop-down-modal-open > div > div.balance-info-block__data > div.balance-info-block__balance > span',
                '.balance-info-block__balance span',
            ]
            deposit_element = None
            for selector in deposit_selectors:
                try:
                    deposit_element = driver.find_element(By.CSS_SELECTOR, selector)
                    break
                except NoSuchElementException:
                    continue
            
            if not deposit_element:
                log("Could not find deposit element for Martingale check")
                LAST_TRADE_DETAILS = {'asset': None, 'action': None, 'amount': None}
                return
            
            current_deposit = float(deposit_element.text.replace(',', '').replace('$', '').replace('\u202f', ''))
        except Exception as e:
            log(f"Error reading deposit for Martingale: {e}")
            LAST_TRADE_DETAILS = {'asset': None, 'action': None, 'amount': None}
            return
        
        try:
            # Try to switch to 'Closed Trades' tab
            try:
                closed_tab = driver.find_element(By.CSS_SELECTOR, 
                    '#bar-chart > div > div > div.right-widget-container > div > div.widget-slot__header > div.divider > ul > li:nth-child(2) > a')
                closed_tab_parent = closed_tab.find_element(By.XPATH, '..')
                was_closed_tab_active = (closed_tab_parent.get_attribute('class') == 'active')
                if not was_closed_tab_active:
                    closed_tab.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass
            
            await set_amount_icon(driver)
            
            closed_trades = driver.find_elements(By.CLASS_NAME, 'deals-list__item')
            trade_result_processed = False
            
            if closed_trades:
                last_trade = closed_trades[0].text.split('\n')
                
                try:
                    # Parse trade result - indices may vary
                    # Typical format: [Asset, Time, Amount, Profit/Loss, Result, ...]
                    is_win = False
                    is_draw = False
                    is_loss = False
                    
                    # Check profit/loss columns (indices 3 and 4 typically)
                    if len(last_trade) >= 5:
                        profit_text = last_trade[4].replace('$', '').replace('\u202f', '').replace(',', '').strip()
                        result_text = last_trade[3].replace('$', '').replace('\u202f', '').replace(',', '').strip()
                        
                        try:
                            profit_val = float(profit_text)
                            result_val = float(result_text)
                            
                            if profit_val > 0:
                                is_win = True
                            elif result_val == 0 and profit_val == 0:
                                is_draw = True
                            else:
                                is_loss = True
                        except ValueError:
                            is_loss = True
                    
                    # --- UNCONDITIONAL MARTINGALE LOGIC ---
                    
                    # 1. Win/Draw: Reset Series
                    if is_win or is_draw:
                        log(f"MARTINGALE: {'Win' if is_win else 'Draw'} on {asset_name_from_trade} (Step {MARTINGALE_STEP}). Resetting series.")
                        MARTINGALE_STEP = 0
                        MARTINGALE_ACTIVE_ASSET = None
                        MARTINGALE_ACTIVE_ACTION = None
                        MARTINGALE_INITIAL_AMOUNT_SET = False
                        trade_result_processed = True
                    
                    # 2. Loss: Advance Step
                    elif is_loss and MARTINGALE_ACTIVE_ASSET is not None:
                        next_step_index = MARTINGALE_STEP + 1
                        
                        if next_step_index < len(MARTINGALE_LIST):
                            next_amount = MARTINGALE_LIST[next_step_index]
                            
                            if next_amount > current_deposit:
                                log(f'MARTINGALE: Deposit ({current_deposit}) < next step ({next_amount}). Resetting.')
                                MARTINGALE_STEP = 0
                                MARTINGALE_ACTIVE_ASSET = None
                                MARTINGALE_ACTIVE_ACTION = None
                                MARTINGALE_INITIAL_AMOUNT_SET = False
                            else:
                                MARTINGALE_STEP = next_step_index
                                MARTINGALE_LAST_LOSS_TIME[asset_name_from_trade] = datetime.now()
                                log(f"MARTINGALE: Loss on {asset_name_from_trade}. Next: {next_amount} (Step {MARTINGALE_STEP}). Waiting for delay...")
                        else:
                            log(f"MARTINGALE: End of list reached (Step {MARTINGALE_STEP}). Resetting.")
                            MARTINGALE_STEP = 0
                            MARTINGALE_ACTIVE_ASSET = None
                            MARTINGALE_ACTIVE_ACTION = None
                            MARTINGALE_INITIAL_AMOUNT_SET = False
                        
                        trade_result_processed = True
                    
                    # 3. Loss on non-Martingale trade
                    elif is_loss and MARTINGALE_ACTIVE_ASSET is None:
                        log("MARTINGALE: Loss on unmanaged trade. Resetting.")
                        MARTINGALE_STEP = 0
                        MARTINGALE_INITIAL_AMOUNT_SET = False
                        trade_result_processed = True
                    
                    if trade_result_processed:
                        LAST_TRADE_DETAILS = {'asset': None, 'action': None, 'amount': None}
                
                except Exception as e:
                    log(f"Martingale step update error: {e}")
            
            # Switch back to 'Open Trades' tab
            try:
                open_tab = driver.find_element(By.CSS_SELECTOR,
                    '#bar-chart > div > div > div.right-widget-container > div > div.widget-slot__header > div.divider > ul > li:nth-child(1) > a')
                open_tab_parent = open_tab.find_element(By.XPATH, '..')
                if open_tab_parent.get_attribute('class') != 'active':
                    open_tab.click()
            except Exception:
                pass
                
        except Exception as e:
            log(f"Martingale result check error: {e}")
    
    # --- Phase 2: IMMEDIATE MARTINGALE EXECUTION ---
    if SETTINGS.get('MARTINGALE_ENABLED') and MARTINGALE_ACTIVE_ASSET is not None:
        asset = MARTINGALE_ACTIVE_ASSET
        action = MARTINGALE_ACTIVE_ACTION
        amount_to_set = MARTINGALE_LIST[MARTINGALE_STEP]
        delay_seconds = SETTINGS.get('MARTINGALE_LOSS_DELAY_SECONDS', 10)
        
        # Check delay
        last_loss_time = MARTINGALE_LAST_LOSS_TIME.get(asset, datetime.min)
        if last_loss_time + timedelta(seconds=delay_seconds) > datetime.now():
            time_left = (last_loss_time + timedelta(seconds=delay_seconds) - datetime.now()).total_seconds()
            log(f"Immediate Martingale for {asset} delayed. Waiting {time_left:.2f}s...")
            return
        
        log(f"Executing Immediate Martingale: {action.upper()} on {asset} with ${amount_to_set} (Step {MARTINGALE_STEP})")
        
        switch = await switch_to_asset(driver, asset)
        if switch:
            try:
                await set_amount_icon(driver)
                await set_amount_on_ui(driver, amount_to_set)
                
                order_created = await create_order(driver, action, asset, martingale_override=True)
                
                if order_created:
                    log(f"Immediate Martingale executed. Waiting for result...")
                    await asyncio.sleep(1)
                    return
            except Exception as e:
                log(f"Error executing Immediate Martingale: {e}")
                # Reset on failure
                MARTINGALE_STEP = 0
                MARTINGALE_ACTIVE_ASSET = None
                MARTINGALE_ACTIVE_ACTION = None
                MARTINGALE_INITIAL_AMOUNT_SET = False
        else:
            log(f"Could not switch to {asset} for Martingale. Resetting.")
            MARTINGALE_STEP = 0
            MARTINGALE_ACTIVE_ASSET = None
            MARTINGALE_ACTIVE_ACTION = None
            MARTINGALE_INITIAL_AMOUNT_SET = False
    
    # --- Phase 3: STANDARD STRATEGY EXECUTION ---
    if SETTINGS.get('MARTINGALE_ENABLED') and MARTINGALE_ACTIVE_ASSET is not None:
        return  # Martingale active, wait for Phase 2
    
    # Ensure initial amount is set for new series
    if SETTINGS.get('MARTINGALE_ENABLED') and not MARTINGALE_INITIAL_AMOUNT_SET:
        try:
            await set_amount_icon(driver)
            await set_amount_on_ui(driver, MARTINGALE_LIST[0])
            MARTINGALE_INITIAL_AMOUNT_SET = True
            log(f"Initial Martingale amount set to {MARTINGALE_LIST[0]}")
        except Exception as e:
            log(f"Error setting initial Martingale amount: {e}")
            return
    
    for asset, candles in CANDLES.items():
        action = None
        sstrategy = None
        
        # 1. Server Strategies
        if SETTINGS.get('USE_SERVER_STRATEGIES') and asset in SERVER_STRATEGIES and SERVER_STRATEGIES[asset] and PERIOD == 60:
            for sstrategy in SERVER_STRATEGIES[asset]:
                action = await check_strategies(candles, sstrategy=sstrategy)
                if action:
                    break
        
        # 2. Local Strategy
        if not action:
            action = await check_strategies(candles, sstrategy=None)
        
        if not action:
            continue
        
        # Execute Order
        order_created = await create_order(driver, action, asset, sstrategy=sstrategy)
        if order_created:
            await asyncio.sleep(1)
            return  # One order per cycle


# --- Backtesting ---

async def get_candles_yfinance(email: str, asset: str, timeframe: str) -> List[List]:
    """Fetches historical candles from remote server for backtesting."""
    response = requests.get(CANDLES_URL, params={
        'asset': asset, 'email': email, 'timeframe': timeframe, 'size': 10000
    }, timeout=30)
    if response.status_code != 200:
        raise Exception(response.json().get('error', 'Unknown error'))
    
    # Format: [['', '', close_value], ...] to fit strategies where 'close' is index 2
    return [['', '', c] for c in response.json().get(asset, [])]


async def backtest(email: str, timeframe: str = '1m') -> None:
    """Runs a backtest on historical data using the current strategy."""
    log(f'--- Starting Backtest on {timeframe} timeframe ---')
    
    try:
        assets_response = requests.get(ASSETS_URL, params={'email': email}, timeout=30)
        assets_response.raise_for_status()
        assets = assets_response.json().get('assets', [])
    except Exception as e:
        log(f"Error fetching assets for backtest: {e}")
        return
    
    PROFITS = []
    
    for asset in assets:
        await asyncio.sleep(0.6)
        
        try:
            candles = await get_candles_yfinance(email, asset, timeframe=timeframe)
        except Exception as e:
            log(f'Backtest {asset}: Error fetching candles ({e}). Skipping.')
            continue
        
        if not candles:
            log(f'Backtest {asset}: No candles. Skipping.')
            continue
        
        size = max(SETTINGS['SLOW_MA'], SETTINGS['RSI_PERIOD']) + 11
        if len(candles) < size + 3:
            log(f'Backtest {asset}: Not enough data (need {size+3}, have {len(candles)}).')
            continue
        
        actions = {}
        for i in range(size, len(candles) - 3):
            candles_part = candles[i-size:i+1]
            action = await check_strategies(candles_part)
            
            if action:
                if SETTINGS['VICE_VERSA']:
                    action = 'call' if action == 'put' else 'put'
                actions[i] = action
        
        try:
            per = int(len(candles) / len(actions))
        except ZeroDivisionError:
            per = 0
        
        log(f'Backtest {asset} ({timeframe}): Trades: {len(actions)}. Freq: 1 per {per} candles.')
        
        for estimation in [1, 2, 3]:
            wins = draws = total = 0
            
            for trade_idx, action in actions.items():
                target_idx = trade_idx + estimation
                if target_idx >= len(candles):
                    continue
                
                total += 1
                trigger_price = candles[trade_idx][2]
                expiration_price = candles[target_idx][2]
                
                if trigger_price == expiration_price:
                    draws += 1
                elif action == 'call' and trigger_price < expiration_price:
                    wins += 1
                elif action == 'put' and trigger_price > expiration_price:
                    wins += 1
            
            try:
                denominator = total - draws
                profit = (wins * 100) // denominator if denominator > 0 else 0
                PROFITS.append(profit)
                log(f'  Est. {estimation} candles: Wins: {wins}, Draws: {draws}. Profit: {profit}%')
            except ZeroDivisionError:
                log(f'  Est. {estimation} candles: No decisive trades.')
                continue
    
    if PROFITS:
        avg_profit = sum(PROFITS) // len(PROFITS)
        log(f'Backtest average profit: {avg_profit}%')
    else:
        log('No successful backtest results.')
    
    log('--- Backtest ended ---')


# --- Main Entry Point ---

async def main() -> None:
    """The main entry point for the trading bot."""
    log("Starting trading bot...")
    
    # 1. Read Settings
    read_settings()
    
    # 2. Configure Environment
    await set_remote_debugging_allowed()
    
    # 3. Initialize WebDriver
    driver = None
    try:
        driver = await get_driver()
        driver.get(URL)
        await asyncio.sleep(5)
        log("Browser launched. Please login if necessary.")
    except Exception as e:
        log(f"Failed to initialize WebDriver: {e}")
        return
    
    # 4. Main Trading Loop
    try:
        while True:
            await check_deposit(driver)
            
            if TRADING_ALLOWED:
                await websocket_log(driver)
                await check_indicators(driver)
            else:
                log("Trading stopped (Stop Loss/Take Profit reached).")
            
            await asyncio.sleep(0.5)
    
    except KeyboardInterrupt:
        log("Bot stopped by user.")
    except Exception as e:
        log(f"Unexpected error in main loop: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        log("Browser closed. Exiting.")


if __name__ == '__main__':
    # Create default settings.txt if not exists
    if not os.path.exists(SETTINGS_PATH):
        try:
            default_content = """# Trading Bot Settings (Set to True/False or the specified value)
FAST_MA=5
SLOW_MA=20
FAST_MA_TYPE=SMA
SLOW_MA_TYPE=SMA
RSI_ENABLED=False
RSI_PERIOD=14
RSI_UPPER=70
RSI_CALL_SIGN=>
COUNT_BULLISH=0
COUNT_BEARISH=0
MARTINGALE_ENABLED=True
MARTINGALE_LOSS_DELAY_SECONDS=10
MARTINGALE_LIST=1,3,10,18,39,80
TAKE_PROFIT_ENABLED=False
TAKE_PROFIT=100
STOP_LOSS_ENABLED=False
STOP_LOSS=50
VICE_VERSA=False
BEGINNING_CANDLE_ORDER=False
USE_SERVER_STRATEGIES=False
BACKTEST=False
BACKTEST_TIMEFRAME=1m
MIN_PAYOUT=70
"""
            with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
                f.write(default_content)
            log(f"Created default settings file: {SETTINGS_PATH}. Please review and configure.")
        except Exception as e:
            log(f"Could not create settings file: {e}. Using defaults.")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Program interrupted and closed.")
    except RuntimeError as e:
        if "cannot run" in str(e).lower() and "running event loop" in str(e).lower():
            log("Running asyncio.run in an already running event loop. Exiting.")
        else:
            raise