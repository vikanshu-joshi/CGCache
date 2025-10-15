from flask import Flask, request, jsonify
import json
import argparse

app = Flask(__name__)

# In-memory cache to store responses
cache_store = {}


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
    """
    try:
        # Get cache key from query parameters
        cache_key = request.args.get('cacheKey')
        
        if not cache_key:
            return jsonify({'error': 'cacheKey query parameter is required'}), 400
        
        # Get the entire request body
        request_body = request.data.decode('utf-8')
        
        if not request_body:
            return jsonify({'error': 'Request body is empty'}), 400
        
        # Store the entire response as-is (as string)
        cache_store[cache_key] = request_body
        
        return jsonify({
            'message': 'Cache saved successfully',
            'cacheKey': cache_key
        }), 200
        
    except Exception as e:
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
            return jsonify({'error': 'No JSON data provided'}), 400
        
        cache_key = data.get('cacheKey')
        
        if not cache_key:
            return jsonify({'error': 'cacheKey is required'}), 400
        
        if cache_key not in cache_store:
            return jsonify({'error': 'Cache key not found'}), 404
        
        # Get the cached response string
        cached_response_string = cache_store[cache_key]
        
        # Try to parse and return as JSON, if it fails return as plain text
        try:
            cached_response_json = json.loads(cached_response_string)
            return jsonify(cached_response_json), 200
        except:
            # If not valid JSON, return as plain text
            return cached_response_string, 200, {'Content-Type': 'text/plain'}
        
    except Exception as e:
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
            cache_store.clear()
            return jsonify({
                'message': 'All cache cleared successfully'
            }), 200
        
        # Clear specific cache key
        cache_key = data.get('cacheKey')
        
        if cache_key not in cache_store:
            return jsonify({'error': 'Cache key not found'}), 404
        
        del cache_store[cache_key]
        
        return jsonify({
            'message': 'Cache cleared successfully',
            'cacheKey': cache_key
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/keys', methods=['GET'])
def get_all_keys():
    """
    Get a list of all cache keys currently stored.
    Returns an array of cache keys and the total count.
    """
    try:
        all_keys = list(cache_store.keys())
        
        return jsonify({
            'keys': all_keys,
            'count': len(all_keys)
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Run Flask cache server')
    parser.add_argument('--port', type=int, default=80, 
                        help='Port to run the server on (default: 80)')
    parser.add_argument('--debug', action='store_true', 
                        help='Run in debug mode (default: False)')
    args = parser.parse_args()
    
    # Production configuration
    app.run(debug=args.debug, host='0.0.0.0', port=args.port, threaded=True)


