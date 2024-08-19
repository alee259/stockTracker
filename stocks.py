from flask import Flask, render_template, request, send_from_directory
import os
import mysql.connector
import datetime
import matplotlib.pyplot as plt
import seaborn as sns
import requests
import pandas as pd
from dotenv import load_dotenv

app = Flask(__name__)
load_dotenv()

# MySQL db information
DATABASE_CONFIG = {
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'host': os.getenv('DB_HOST'),
    'database': os.getenv('DB_NAME'),
    'port': os.getenv('DB_PORT')
}

print("Database User:", os.getenv('DB_USER'))
print("Database Password:", os.getenv('DB_PASSWORD'))
print("Database Host:", os.getenv('DB_HOST'))
print("Database Name:", os.getenv('DB_NAME'))
print("Database Port:", os.getenv('DB_PORT'))
print("API Key:", os.getenv('API_KEY'))

#directory setup
BASE_DIRECTORY = os.path.dirname(os.path.abspath(__file__))  # Directory where the script is located
PDF_DIRECTORY = os.path.join(BASE_DIRECTORY, "Reports")
GRAPH_DIRECTORY = os.path.join(BASE_DIRECTORY, "static", "Graphs")

# API Key
API_KEY = os.getenv('API_KEY')

#creating the necessary table if it already doesnt exist
def create_table():
    conn = mysql.connector.connect(**DATABASE_CONFIG)
    cursor = conn.cursor()
    #SQL query 
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tickers ( 
            id INT AUTO_INCREMENT PRIMARY KEY,
            symbol VARCHAR(10) NOT NULL,
            date DATE NOT NULL,
            close FLOAT,
            high FLOAT,
            low FLOAT,
            open FLOAT,
            volume BIGINT,
            UNIQUE(symbol, date)
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()

#this function is to check current tickers already stored, and update them. It also updates the graphs if needed
def update_existing_tickers():
    existing_tickers = get_existing_tickers()
    for ticker in existing_tickers:
        eod_data = fetch_eod_data(ticker)
        if eod_data is not None:
            add_or_update_ticker(ticker, eod_data)
            df = fetch_one_month_data(ticker)
            if df is not None:
                save_to_db(df, ticker)  # Save the data to the database
                generate_graph(df, ticker)  # Generate and save the graph

#this function is meant to save all the data we called from the API to store it into the db
def save_to_db(df, ticker):
    if 'date' not in df.columns:
        df = df.reset_index()  # Reset index if 'date' is an index

    df.columns = df.columns.str.strip()

    required_columns = ['date', 'open', 'high', 'low', 'close', 'volume']
    missing_columns = [col for col in required_columns if col not in df.columns]
    
    if missing_columns:
        raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")
    
    df['symbol'] = ticker
    df = df[['symbol', 'date', 'open', 'high', 'low', 'close', 'volume']]

    conn = mysql.connector.connect(**DATABASE_CONFIG)
    cursor = conn.cursor()

    for _, row in df.iterrows():
        cursor.execute('''
            INSERT INTO tickers (symbol, date, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                open=VALUES(open), high=VALUES(high), low=VALUES(low), close=VALUES(close), volume=VALUES(volume)
        ''', tuple(row))

    conn.commit()
    cursor.close()
    conn.close()

#this function checks if we need to update any tickers while the app is running
def add_or_update_ticker(ticker_symbol, data):
    conn = mysql.connector.connect(**DATABASE_CONFIG)
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO tickers (symbol, date, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            open=VALUES(open), high=VALUES(high), low=VALUES(low), close=VALUES(close), volume=VALUES(volume)
    ''', (ticker_symbol, data['date'], data['open'], data['high'], data['low'], data['close'], data['volume']))

    conn.commit()
    cursor.close()
    conn.close()

#this function retrieves the tickers from the db
def get_existing_tickers():
    conn = mysql.connector.connect(**DATABASE_CONFIG)
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT symbol FROM tickers')
    tickers = cursor.fetchall()
    cursor.close()
    conn.close()
    return [ticker[0] for ticker in tickers]

#this function retrieves the most recent, or the last open stock day's data
def get_most_recent_ticker_data():
    conn = mysql.connector.connect(**DATABASE_CONFIG)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT t1.symbol, t1.date, t1.open, t1.high, t1.low, t1.close, t1.volume
        FROM tickers t1
        INNER JOIN (
            SELECT symbol, MAX(date) AS max_date
            FROM tickers
            GROUP BY symbol
        ) t2 ON t1.symbol = t2.symbol AND t1.date = t2.max_date
    ''')
    
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    
    tickers_data = {}
    for row in rows:
        symbol, date, open_, high, low, close, volume = row
        if symbol not in tickers_data:
            tickers_data[symbol] = []
        tickers_data[symbol].append({
            'date': date,
            'open': open_,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        })
    
    return tickers_data

#this makes an api call to retrieve the data from the end of a trading day
def fetch_eod_data(ticker):
    BASE_URL = "https://api.polygon.io/v2/aggs/ticker/"
    url = f"{BASE_URL}{ticker}/prev?apiKey={API_KEY}"
    response = requests.get(url)
    
    if response.status_code == 200:
        data = response.json()
        if 'results' in data and len(data['results']) > 0:
            result = data['results'][0]
            date = datetime.datetime.fromtimestamp(result['t'] / 1000.0).strftime('%Y-%m-%d')
            return {
                'date': date,
                'open': result['o'],
                'high': result['h'],
                'low': result['l'],
                'close': result['c'],
                'volume': result['v']
            }
        else:
            print(f"No data available for {ticker} on the previous trading day.")
            return None
    elif response.status_code == 403:
        print("Access forbidden. Check your API key and permissions.")
    elif response.status_code == 404:
        print(f"Data not found for ticker {ticker}. Verify ticker symbol.")
    else:
        print(f"Failed to fetch data: {response.status_code} - {response.text}")
    
    return None

#this function makes an api call to get a full month's worsth of data
def fetch_one_month_data(ticker):
    BASE_URL = "https://api.polygon.io/v2/aggs/ticker/"
    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=30)
    
    url = f"{BASE_URL}{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}?apiKey={API_KEY}"
    response = requests.get(url)
    
    if response.status_code == 200:
        data = response.json()
        if 'results' in data and len(data['results']) > 0:
            results = data['results']
            df = pd.DataFrame(results)
            df['date'] = pd.to_datetime(df['t'], unit='ms')
            df.set_index('date', inplace=True)
            df = df.rename(columns={'c': 'close', 'h': 'high', 'l': 'low', 'o': 'open', 'v': 'volume'})
            df_dropped = df.drop(columns=['vw', 't', 'n'])
            return df_dropped
        else:
            print(f"No historical data available for {ticker} for the past month.")
            return None
    elif response.status_code == 403:
        print("Access forbidden. Check your API key and permissions.")
    elif response.status_code == 404:
        print(f"Data not found for ticker {ticker}. Verify ticker symbol.")
    else:
        print(f"Failed to fetch data: {response.status_code} - {response.text}")
    
    return None

#makes graphs
def generate_graph(df, ticker):
    print("Making graph")
    if not os.path.exists(GRAPH_DIRECTORY):
        os.makedirs(GRAPH_DIRECTORY)
    
    plt.figure(figsize=(10, 6))
    sns.lineplot(data=df, x=df.index, y='close', label='Close Price')
    plt.title(f'{ticker} - Last Month\'s Close Price')
    plt.xlabel('Date')
    plt.ylabel('Close Price')
    plt.legend()
    plt.grid(True)
    graph_filename = f"{ticker}_last_month.png"
    graph_path = os.path.join(GRAPH_DIRECTORY, graph_filename)
    plt.savefig(graph_path)
    plt.close()
    return graph_filename

#flask route for index
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        tickers = request.form['tickers']
        tickers = [ticker.strip().upper() for ticker in tickers.split(',')]
        
        all_data = []
        for ticker in tickers:
            eod_data = fetch_eod_data(ticker)
            df = fetch_one_month_data(ticker)
            
            if df is not None and eod_data is not None:
                add_or_update_ticker(ticker, eod_data)
                save_to_db(df, ticker)
                graph_filename = generate_graph(df, ticker)
                
                all_data.append({
                    'ticker': ticker,
                    'graph_path': graph_filename,
                    'eod_data': eod_data,
                    'data': df
                })
        
        return render_template('index.html', all_data=all_data, existing_data=get_most_recent_ticker_data())
    
    existing_data = get_most_recent_ticker_data()
    return render_template('index.html', all_data=None, existing_data=existing_data)

#flask route for the graphs
@app.route('/static/Graphs/<filename>')
def serve_graph(filename):
    return send_from_directory(GRAPH_DIRECTORY, filename)

#starting up the app
if __name__ == '__main__':
    create_table()
    update_existing_tickers()  # Update the existing tickers with the latest EOD data
    app.run(debug=True)