from flask import Flask, request, jsonify
import json
import argparse
import threading
import time
import logging
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Also configure Flask's logger
app.logger.setLevel(logging.INFO)

# In-memory cache to store responses
cache_store = {}

# Store cache expiration times (cache_key -> expiration_timestamp)
cache_expiration = {}

# Lock for thread-safe operations
cache_lock = threading.Lock()


def cleanup_expired_cache():
    """
    Background job that runs continuously to delete expired cache entries.
    Checks every 15 minutes for entries that have exceeded their 15-minute TTL.
    """
    while True:
        try:
            time.sleep(900)  # Check every 15 minutes
            current_time = datetime.now()
            
            logger.info("[Cleanup Job] Running cache cleanup check...")
            
            with cache_lock:
                # Find all expired cache keys
                expired_keys = [
                    key for key, expiration_time in cache_expiration.items()
                    if current_time >= expiration_time
                ]
                
                # Delete expired entries
                if expired_keys:
                    for key in expired_keys:
                        if key in cache_store:
                            del cache_store[key]
                            logger.info(f"[Cleanup Job] Deleted expired cache key: '{key}'")
                        if key in cache_expiration:
                            del cache_expiration[key]
                    
                    logger.info(f"[Cleanup Job] Cleanup complete. Total keys deleted: {len(expired_keys)}")
                else:
                    logger.info("[Cleanup Job] No expired cache entries found")
                        
        except Exception as e:
            logger.error(f"[Cleanup Job] Error during cleanup: {str(e)}", exc_info=True)


def start_cleanup_job():
    """
    Start the background cleanup job in a daemon thread.
    Daemon thread will automatically exit when the main program exits.
    """
    cleanup_thread = threading.Thread(target=cleanup_expired_cache, daemon=True)
    cleanup_thread.start()
    logger.info("[Cleanup Job] Background cleanup job started successfully")


# Flag to ensure cleanup job starts only once
_cleanup_job_started = False
_cleanup_job_lock = threading.Lock()


def ensure_cleanup_job_started():
    """
    Ensure the cleanup job is started (called on first request).
    This ensures it works with both Flask dev server and production WSGI servers.
    """
    global _cleanup_job_started
    with _cleanup_job_lock:
        if not _cleanup_job_started:
            start_cleanup_job()
            _cleanup_job_started = True


# Hook to start cleanup job on first request (works with Gunicorn)
@app.before_request
def initialize():
    ensure_cleanup_job_started()


def get_request_data():
    """
    Helper function to parse request data regardless of Content-Type.
    Supports both application/json and text/plain content types.
    """
    # Try to get JSON with force=True to ignore Content-Type
    try:
        return request.get_json(force=True)
    except:
        # If that fails, try to parse the raw data
        try:
            return json.loads(request.data.decode('utf-8'))
        except:
            return None


@app.route('/save', methods=['POST'])
def save_cache():
    """
    Save entire request body in cache with the given cache key.
    Cache key should be provided as a query parameter: ?cacheKey=<key>
    The entire request body will be stored as-is.
    Cache entries automatically expire after 15 minutes.
    """
    try:
        # Get cache key from query parameters
        cache_key = request.args.get('cacheKey')
        
        if not cache_key:
            logger.warning("[/save] Request missing cacheKey parameter")
            return jsonify({'error': 'cacheKey query parameter is required'}), 400
        
        # Get the entire request body
        request_body = request.data.decode('utf-8')
        
        if not request_body:
            logger.warning(f"[/save] Empty request body for cache key: '{cache_key}'")
            return jsonify({'error': 'Request body is empty'}), 400
        
        # Store the entire response as-is (as string) with thread safety
        with cache_lock:
            is_update = cache_key in cache_store
            cache_store[cache_key] = request_body
            # Set expiration time to 15 minutes from now
            expiration_time = datetime.now() + timedelta(minutes=15)
            cache_expiration[cache_key] = expiration_time
        
        body_size = len(request_body)
        action = "Updated" if is_update else "Saved"
        logger.info(f"[/save] {action} cache key: '{cache_key}' | Size: {body_size} bytes | Expires at: {expiration_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        return jsonify({
            'message': 'Cache saved successfully',
            'cacheKey': cache_key,
            'expiresIn': '15 minutes'
        }), 200
        
    except Exception as e:
        logger.error(f"[/save] Error saving cache: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/get', methods=['POST'])
def get_cache():
    """
    Retrieve a cached response by cache key.
    Request body should contain:
    - cacheKey: string identifier for the cache entry
    """
    try:
        data = get_request_data()
        
        if not data:
            logger.warning("[/get] Request missing JSON data")
            return jsonify({'error': 'No JSON data provided'}), 400
        
        cache_key = data.get('cacheKey')
        
        if not cache_key:
            logger.warning("[/get] Request missing cacheKey field")
            return jsonify({'error': 'cacheKey is required'}), 400
        
        # Thread-safe cache access
        with cache_lock:
            if cache_key not in cache_store:
                logger.warning(f"[/get] Cache key not found: '{cache_key}'")
                return jsonify({'error': 'Cache key not found'}), 404
            
            # Get the cached response string
            cached_response_string = cache_store[cache_key]
            expiration_time = cache_expiration.get(cache_key)
        
        response_size = len(cached_response_string)
        time_remaining = None
        if expiration_time:
            time_remaining = (expiration_time - datetime.now()).total_seconds()
        
        logger.info(f"[/get] Retrieved cache key: '{cache_key}' | Size: {response_size} bytes | Time remaining: {int(time_remaining)}s")
        
        # Try to parse and return as JSON, if it fails return as plain text
        try:
            cached_response_json = json.loads(cached_response_string)
            return jsonify(cached_response_json), 200
        except:
            # If not valid JSON, return as plain text
            return cached_response_string, 200, {'Content-Type': 'text/plain'}
        
    except Exception as e:
        logger.error(f"[/get] Error retrieving cache: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/clear', methods=['POST'])
def clear_cache():
    """
    Clear cache entries. Can clear all cache or specific cache key.
    Request body (optional):
    - cacheKey: string identifier to clear specific entry (if not provided, clears all cache)
    """
    try:
        data = get_request_data()
        
        # If no data or no cacheKey provided, clear all cache
        if not data or not data.get('cacheKey'):
            with cache_lock:
                total_keys = len(cache_store)
                cache_store.clear()
                cache_expiration.clear()
            
            logger.info(f"[/clear] Cleared ALL cache entries | Total keys cleared: {total_keys}")
            
            return jsonify({
                'message': 'All cache cleared successfully',
                'keysCleared': total_keys
            }), 200
        
        # Clear specific cache key
        cache_key = data.get('cacheKey')
        
        with cache_lock:
            if cache_key not in cache_store:
                logger.warning(f"[/clear] Attempted to clear non-existent cache key: '{cache_key}'")
                return jsonify({'error': 'Cache key not found'}), 404
            
            del cache_store[cache_key]
            # Also remove expiration entry if exists
            if cache_key in cache_expiration:
                del cache_expiration[cache_key]
        
        logger.info(f"[/clear] Cleared cache key: '{cache_key}'")
        
        return jsonify({
            'message': 'Cache cleared successfully',
            'cacheKey': cache_key
        }), 200
        
    except Exception as e:
        logger.error(f"[/clear] Error clearing cache: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/keys', methods=['GET'])
def get_all_keys():
    """
    Get a list of all cache keys currently stored.
    Returns an array of cache keys and the total count.
    """
    try:
        with cache_lock:
            all_keys = list(cache_store.keys())
        
        logger.info(f"[/keys] Retrieved all cache keys | Total keys: {len(all_keys)}")
        
        return jsonify({
            'keys': all_keys,
            'count': len(all_keys)
        }), 200
        
    except Exception as e:
        logger.error(f"[/keys] Error retrieving keys: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Run Flask cache server')
    parser.add_argument('--port', type=int, default=80, 
                        help='Port to run the server on (default: 80)')
    parser.add_argument('--debug', action='store_true', 
                        help='Run in debug mode (default: False)')
    args = parser.parse_args()
    
    # Log startup information
    logger.info("=" * 60)
    logger.info("CGCache Server Starting...")
    logger.info(f"Port: {args.port}")
    logger.info(f"Debug Mode: {args.debug}")
    logger.info(f"Cache Expiration Time: 15 minutes")
    logger.info(f"Cleanup Job Interval: 15 minutes")
    logger.info("=" * 60)
    
    # Start the background cleanup job
    start_cleanup_job()
    
    # Production configuration
    app.run(debug=args.debug, host='0.0.0.0', port=args.port, threaded=True)


