import requests
import json
import time
import hmac
import hashlib
import base64
import bitso
from decimal import Decimal
import logging
from os import environ


class TaurosPrivate:

    def __init__(self, key, secret, prod=True):
        self.key = key
        self.secret = secret
        self.base_url = 'https://api.tauros.io' if prod else 'https://api.staging.tauros.io'

    def _get_signature(self, path, data, nonce, method='post'):
        request_data = json.dumps(data, separators=(',', ':'))
        message = str(nonce) + method.upper() + path + str(request_data)
        api_sha256 = hashlib.sha256(message.encode()).digest()
        api_hmac = hmac.new(base64.b64decode(self.secret), api_sha256, hashlib.sha512)
        api_signature = base64.b64encode(api_hmac.digest())
        signature = api_signature.decode()
        return signature

    def _request(self, path, data={}, query_params={}, method='post'):
        nonce = str(int(1000*time.time()))
        signature = self._get_signature(
            path=path,
            data=data,
            nonce=nonce,
            method=method,
        )
        headers = {
            'Authorization': 'Bearer {}'.format(self.key),
            'Taur-Signature': signature,
            'Taur-Nonce': nonce,
            'Content-Type': 'application/json',
        }
        return requests.request(
            method=method,
            url=self.base_url+path,
            data=json.dumps(data),
            params=query_params,
            headers=headers,
        ).json()

    def place_order(self, order):
        path = '/api/v1/trading/placeorder/'
        return self._request(path=path, data=order)

    def get_orders(self, market=None):
        path = '/api/v1/trading/myopenorders/'
        params = {}
        if market:
            params['market'] = market
        return self._request(path=path, query_params=params, method='get')

    def close_order(self, order_id):
        path = '/api/v1/trading/closeorder/'
        data = {
            'id': order_id,
        }
        return self._request(path=path, data=data)

    def get_wallet(self, coin):
        path = '/api/v1/data/getbalance/'
        data = {
            'coin': coin,
        }
        return self._request(path=path, query_params=data, method='get')


class TaurosPublic():

    def __init__(self,prod=True):
        self.base_url = 'https://api.tauros.io/api' if prod else 'https://api.staging.tauros.io/api'

    def _request(self, path, params={}):
        return requests.get(
            url=self.base_url+path,
            params=params,
        ).json()

    def get_order_book(self, market='BTC-MXN'):
        path = '/v1/trading/orders/'
        params = {
            'market': market
        }
        return self._request(path=path, params=params)



tauros_key = environ.get('TAUR_API_KEY')
tauros_secret = environ.get('TAUR_API_KEY')

if not tauros_key or not tauros_secret:
    raise ValueError('Tauros credentials not fund')

tauros = TaurosPrivate(key=tauros_key, secret=tauros_secret, prod=False)

tauros_public = TaurosPublic(prod=False)

bisto_api = bitso.Api()

ORDER_PRICE_DELTA = Decimal('1')

def restart():
    '''
    This function queries all open orders in tauros and closes them.
    '''
    open_orders = tauros.get_orders(market='btc-mxn')
    if not open_orders['success']:
        logging.error(f'Querying open orders fail. Error: {open_orders["msg"]}')
        return
    # Filtering buy orders
    buy_open_orders = list(filter(lambda order: order['side'] == 'BUY', open_orders['data']))
    orders_ids = [order['order_id']  for order in buy_open_orders]
    logging.info(f'Open orders: {orders_ids}')
    orders_closed = 0
    for order_id in orders_ids:
        print("Closing order with id: ", order_id)
        close_order = tauros.close_order(order_id=order_id)
        if not close_order['success']:
            print(f'Close order with id {order_id} failed. Error: ', close_order['msg'])
            continue
        orders_closed += 1
    print(f'{orders_closed} limit orders closed!')

def get_order_price(max_price, ref_price):
    if ref_price > max_price:
        return max_price
    return ref_price + ORDER_PRICE_DELTA

restart()
while True:

    # Getting bitso order book
    bitso_order_book = bisto_api.order_book('btc_mxn')

    bitso_price = None
    for bid in bitso_order_book.bids:
        bid_value = bid.price * bid.amount
        if bid_value >= Decimal('500.00'):
            bitso_price = bid.price
            print('Bitso bid order price: ', bitso_price)
            break

    # Getting tauros order book
    tauros_order_book = tauros_public.get_order_book()
    tauros_price = None
    for bid in tauros_order_book['data']['bids']:
        if Decimal(str(bid['value'])) > Decimal('200.00'):
            tauros_price = Decimal(str(bid['price']))
            print('Tauros bid order price: ', tauros_price)
            break

    if not bitso_price or not tauros_price:
        continue

    # Querying available balance
    mxn_wallet = tauros.get_wallet('mxn')
    if not mxn_wallet['success']:
        continue
    available_mxn_balance = Decimal(mxn_wallet['data']['balances']['available'])

    # Setting order value
    MAX_ORDER_VALUE = Decimal('20000.00')
    if available_mxn_balance > MAX_ORDER_VALUE:
        order_value = MAX_ORDER_VALUE
    else:
        order_value = available_mxn_balance

    order_price = get_order_price(
        max_price=bitso_price,
        ref_price=tauros_price,
    )

    buy_order = {
        "market": "BTC-MXN",
        "amount": str(order_value),
        "is_amount_value": True,
        "side": "BUY",
        "type": "LIMIT",
        "price": str(order_price),
    }

    order_placed = tauros.place_order(order=buy_order)

    if not order_placed['success']:
        print('Could not place buy order. Error: ', order_placed['msg'])
        continue
    order_id = order_placed['data']['id']
    print("Order successfully placed: ", order_placed)
    print('Sleeping 3 minutes')

    time.sleep(60*3)

    close_order = tauros.close_order(order_placed['data']['id'])
    if not close_order['success']:
        print("Order close faild. Error", close_order['msg'])
        restart()