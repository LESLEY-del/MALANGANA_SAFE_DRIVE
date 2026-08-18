from waitress import serve
from backend import app # This imports your Safe Drive Flask app
import logging

# Configure professional logging so you can see if hackers are attacking
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger('waitress')
logger.setLevel(logging.INFO)

print("--- SAFE DRIVE PRODUCTION ENGINE STARTING ---")
print("Target Capacity: 50,000 Users")
print("Status: ONLINE on Port 5000")

# serve() is the engine that prevents the crash
serve(
    app, 
    host='0.0.0.0', 
    port=5000, 
    threads=50,       # Increased from 1 to 50 concurrent workers
    channel_timeout=30, 
    connection_limit=1000, # Prevents overwhelming the RAM
    url_scheme='http'
)