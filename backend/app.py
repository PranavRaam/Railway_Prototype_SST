from flask import Flask, jsonify, send_file, request, Response
from flask_cors import CORS
import os
import main
import threading
import time
import urllib.parse
import logging
from statistical_area_zoom import generate_statistical_area_map
import re

app = Flask(__name__)
# Enable CORS with specific options for production
CORS(app, 
    resources={r"/api/*": {
        "origins": ["https://sst-frontend-swart.vercel.app", "http://localhost:3000", "https://prototype-railway-production.up.railway.app"],
        "supports_credentials": True,
        "allow_headers": ["Content-Type", "Authorization", "Cache-Control", "X-Requested-With"],
        "methods": ["GET", "POST", "OPTIONS", "PUT", "DELETE"],
        "expose_headers": ["Content-Type", "Content-Length", "Content-Disposition", "X-Frame-Options"],
        "max_age": 86400  # Cache preflight requests for 24 hours
    }},
    send_wildcard=False  # Changed to False to prevent CORS issues
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MAP_FILE = "us_20regions_map.html"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
map_generation_lock = threading.Lock()
map_generation_in_progress = False
map_data = None

# Ensure cache directory exists
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)
    logger.info(f"Created cache directory: {CACHE_DIR}")

# Custom middleware to handle CORS for HTML responses
@app.after_request
def add_cors_headers(response):
    # Add CORS headers to all responses
    origin = request.headers.get('Origin', '')
    
    # List of allowed origins
    allowed_origins = ['https://sst-frontend-swart.vercel.app', 'http://localhost:3000', 'https://prototype-railway-production.up.railway.app']
    
    # Allow specific origins only when credentials are involved
    if origin in allowed_origins:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
    elif not response.headers.get('Access-Control-Allow-Credentials'):
        # If credentials aren't being used, we can use wildcard
        response.headers['Access-Control-Allow-Origin'] = '*'
        
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS, PUT, DELETE'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, Cache-Control, X-Requested-With'
    response.headers['Access-Control-Max-Age'] = '86400'  # 24 hours in seconds
    
    # For iframe embedding
    if response.mimetype == 'text/html':
        response.headers['X-Frame-Options'] = 'ALLOW-FROM *'
        response.headers['Content-Security-Policy'] = "frame-ancestors *"
    
    return response

# Specific route for handling preflight CORS OPTIONS requests
@app.route('/api/statistical-area-map/<area_name>', methods=['OPTIONS'])
def options_statistical_area_map(area_name):
    response = Response()
    origin = request.headers.get('Origin', '')
    
    # List of allowed origins
    allowed_origins = ['https://sst-frontend-swart.vercel.app', 'http://localhost:3000', 'https://prototype-railway-production.up.railway.app']
    
    # Allow specific origins when credentials are involved
    if origin in allowed_origins:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
    else:
        # When not a trusted origin, don't support credentials
        response.headers['Access-Control-Allow-Origin'] = '*'
        
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, Cache-Control, X-Requested-With'
    response.headers['Access-Control-Max-Age'] = '86400'  # 24 hours
    return response

@app.route('/api/generate-map', methods=['GET'])
def generate_map():
    global map_generation_in_progress
    
    # Check if map already exists
    if os.path.exists(MAP_FILE):
        logger.info(f"Map already exists at {MAP_FILE}")
        return jsonify({
            "success": True,
            "mapPath": "/api/map",
            "message": "Map already exists"
        })
    
    # Start map generation in background thread if not already in progress
    if not map_generation_in_progress:
        with map_generation_lock:
            map_generation_in_progress = True
        
        def generate_map_task():
            global map_generation_in_progress
            try:
                # Generate the map using the main.py functions
                start_time = time.time()
                logger.info("Starting map generation")
                main.main()
                elapsed_time = time.time() - start_time
                logger.info(f"Map generation completed in {elapsed_time:.2f} seconds")
            except Exception as e:
                logger.error(f"Error during map generation: {e}")
            finally:
                with map_generation_lock:
                    map_generation_in_progress = False
        
        thread = threading.Thread(target=generate_map_task)
        thread.daemon = True
        thread.start()
        logger.info("Map generation thread started")
    
    return jsonify({
        "success": True,
        "mapPath": "/api/map",
        "status": "Map generation started in background",
        "generationInProgress": True
    })

@app.route('/api/map-status', methods=['GET'])
def map_status():
    return jsonify({
        "exists": os.path.exists(MAP_FILE),
        "generationInProgress": map_generation_in_progress
    })

@app.route('/api/map', methods=['GET'])
def get_map():
    # Serve the generated HTML file
    if os.path.exists(MAP_FILE):
        logger.info(f"Serving map file from {MAP_FILE}")
        
        # Read the HTML file
        with open(MAP_FILE, 'r') as f:
            html_content = f.read()
            
        # Remove control elements completely instead of hiding them with CSS
        
        # Remove control layer HTML elements 
        html_content = re.sub(r'<div class="leaflet-control-layers[^>]*>.*?</div>', '', html_content, flags=re.DOTALL)
        
        # Remove top-right control elements
        html_content = re.sub(r'<div class="leaflet-top leaflet-right[^>]*>.*?</div>', '', html_content, flags=re.DOTALL)
        
        # Remove legend elements
        html_content = re.sub(r'<div class="info legend[^>]*>.*?</div>', '', html_content, flags=re.DOTALL)
        
        # Add script to notify parent when map is loaded and fix cross-origin issues
        notification_script = """
        <script>
        document.addEventListener('DOMContentLoaded', function() {
            // Fix for cross-origin security issues
            try {
                // Safe way to get leaflet maps without accessing window properties directly
                setTimeout(function() {
                    var maps = document.querySelectorAll('.leaflet-container');
                    if (maps.length > 0) {
                        console.log('Map optimization complete');
                        
                        // Fix map display issues that might occur in iframes
                        maps.forEach(function(mapContainer) {
                            // Force a resize event on the map container
                            var evt = document.createEvent('UIEvents');
                            evt.initUIEvent('resize', true, false, window, 0);
                            window.dispatchEvent(evt);
                        });
                    }
                    
                    // Notify parent safely using postMessage
                    try {
                        if (window.parent && window.parent !== window) {
                            window.parent.postMessage({type: 'mapLoaded', status: 'success'}, '*');
                        }
                    } catch (e) {
                        console.log('Postmessage not available, continuing silently');
                    }
                }, 1000);
            } catch (e) {
                console.log('Map frame initialization complete');
            }
        });
        </script>
        """
        
        # Add the notification script just before the closing body tag
        html_content = html_content.replace('</body>', notification_script + '</body>')
        
        # Set the appropriate headers for cross-origin iframe embedding
        response = Response(html_content, mimetype='text/html')
        
        # Handle CORS for HTML content - determine appropriate origin
        origin = request.headers.get('Origin', '')
        allowed_origins = ['https://sst-frontend-swart.vercel.app', 'http://localhost:3000', 'https://prototype-railway-production.up.railway.app']
        
        if origin in allowed_origins:
            response.headers['Access-Control-Allow-Origin'] = origin
        else:
            response.headers['Access-Control-Allow-Origin'] = '*'
        
        return response
    else:
        logger.warning("Map file not found")
        return jsonify({
            "success": False,
            "message": "Map not yet generated. Call /api/generate-map first."
        }), 404

@app.route('/api/regions', methods=['GET'])
def get_regions():
    # Fetch region data
    _, _, _, regions = main.define_regions()
    
    # Convert region data to a format suitable for frontend
    region_data = {
        region: {
            "states": data["states"],
            "color": data["color"]
        } for region, data in regions.items()
    }
    
    return jsonify(region_data)

@app.route('/api/static-fallback-map/<area_name>', methods=['GET'])
def get_static_fallback_map(area_name):
    """Provide a simple HTML static map as fallback for areas with CORS/loading issues"""
    try:
        # Decode the URL-encoded area name
        decoded_area_name = urllib.parse.unquote(area_name)
        logger.info(f"Serving static fallback map for: {decoded_area_name}")
        
        # Create a very simple static HTML with minimal dependencies
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Map of {decoded_area_name}</title>
            <style>
                body {{ margin: 0; padding: 0; font-family: Arial, sans-serif; }}
                .map-container {{ 
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    width: 100%;
                    height: 100vh;
                    background-color: #f5f5f5;
                    text-align: center;
                }}
                .area-info {{
                    background-color: white;
                    border-radius: 8px;
                    border: 2px solid #4F46E5;
                    padding: 20px;
                    max-width: 80%;
                    box-shadow: 0 0 10px rgba(0,0,0,0.1);
                }}
                h2 {{ color: #1F2937; margin-top: 0; }}
                p {{ color: #4B5563; }}
            </style>
        </head>
        <body>
            <div class="map-container">
                <div class="area-info">
                    <h2>Map of {decoded_area_name}</h2>
                    <p>This is a simplified view of the {decoded_area_name} area.</p>
                    <p>This static map is shown when the interactive map cannot be loaded.</p>
                    <script>
                        // Notify parent that the map is loaded
                        document.addEventListener('DOMContentLoaded', function() {{
                            try {{
                                setTimeout(function() {{
                                    if (window.parent && window.parent !== window) {{
                                        window.parent.postMessage({{type: 'mapLoaded', status: 'success'}}, '*');
                                    }}
                                }}, 500);
                            }} catch(e) {{
                                console.error('Error sending message to parent:', e);
                            }}
                        }});
                    </script>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Return the HTML content with appropriate headers
        response = Response(html_content, mimetype='text/html')
        
        # Set CORS headers for this response
        origin = request.headers.get('Origin', '*')
        if origin in ['https://sst-frontend-swart.vercel.app', 'http://localhost:3000']:
            response.headers['Access-Control-Allow-Origin'] = origin
        else:
            response.headers['Access-Control-Allow-Origin'] = '*'
            
        response.headers['X-Frame-Options'] = 'ALLOW-FROM *'
        response.headers['Content-Security-Policy'] = "frame-ancestors *"
        response.headers['Cache-Control'] = 'max-age=86400'  # Cache for 24h
        
        return response
    except Exception as e:
        logger.error(f"Error generating static fallback map: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Error generating static fallback map: {str(e)}"
        }), 500

@app.route('/api/statistical-area-map/<area_name>', methods=['GET'])
def get_statistical_area_map(area_name):
    try:
        # Decode the URL-encoded area name
        decoded_area_name = urllib.parse.unquote(area_name)
        logger.info(f"Requested statistical area map for: {decoded_area_name}")
        
        # Parse URL parameters with defaults optimized for performance
        force_regen = request.args.get('force_regen', 'false').lower() == 'true'
        use_cached = request.args.get('use_cached', 'true').lower() == 'true'
        detailed = request.args.get('detailed', 'false').lower() == 'true'
        lightweight = request.args.get('lightweight', 'true').lower() == 'true'
        
        # Parse zoom level (default to 10 for better performance)
        try:
            zoom = int(request.args.get('zoom', '10'))
        except ValueError:
            zoom = 10
            
        # Parse boundary options
        exact_boundary = request.args.get('exact_boundary', 'true').lower() == 'true'
        
        logger.info(f"Map options: force_regen={force_regen}, use_cached={use_cached}, " 
                    f"detailed={detailed}, lightweight={lightweight}, zoom={zoom}, "
                    f"exact_boundary={exact_boundary}")
        
        # Generate or retrieve the map for this statistical area with the specified options
        map_file = generate_statistical_area_map(
            decoded_area_name,
            zoom=zoom,
            exact_boundary=exact_boundary,
            detailed=detailed,
            use_cached=use_cached,
            force_regen=force_regen,
            lightweight=lightweight
        )
        
        # Verify map file exists
        if not os.path.exists(map_file):
            logger.error(f"Generated map file does not exist: {map_file}")
            return jsonify({
                "success": False,
                "message": f"Error: Generated map file not found for {decoded_area_name}"
            }), 500
        
        # Read the HTML file
        with open(map_file, 'r') as f:
            html_content = f.read()
        
        # Add cross-origin safe script to fix security errors and ensure map loads properly
        cross_origin_script = """
        <script>
        // Fix for cross-origin security issues
        document.addEventListener('DOMContentLoaded', function() {
            try {
                // Safe way to get leaflet maps without accessing window properties directly
                setTimeout(function() {
                    var maps = document.querySelectorAll('.leaflet-container');
                    if (maps.length > 0) {
                        console.log('Map optimization complete');
                    }
                    
                    // Notify parent safely using postMessage
                    try {
                        if (window.parent && window.parent !== window) {
                            window.parent.postMessage({type: 'mapLoaded', status: 'success'}, '*');
                        }
                    } catch (e) {
                        console.log('Postmessage not available, continuing silently');
                    }
                }, 500); // Reduced timeout for faster notification
            } catch (e) {
                console.log('Map frame initialization complete');
            }
        });
        </script>
        """
        
        # Insert the script just before the closing body tag if not already present
        if "</script>\n</body>" not in html_content:
            html_content = html_content.replace('</body>', cross_origin_script + '</body>')
        
        # Return the modified HTML content with proper content type and CORS headers
        logger.info(f"Serving modified statistical area map from {map_file}")
        response = Response(html_content, mimetype='text/html')
        
        # Set specific CORS headers for this response
        origin = request.headers.get('Origin', '*')
        if origin in ['https://sst-frontend-swart.vercel.app', 'http://localhost:3000']:
            response.headers['Access-Control-Allow-Origin'] = origin
        else:
            response.headers['Access-Control-Allow-Origin'] = '*'
            
        response.headers['X-Frame-Options'] = 'ALLOW-FROM *'
        response.headers['Content-Security-Policy'] = "frame-ancestors *"
        response.headers['Cache-Control'] = 'max-age=86400' if use_cached else 'no-cache'  # Cache for 24h if using cache
        return response
    except Exception as e:
        logger.error(f"Error generating statistical area map for {area_name}: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Error generating map for {area_name}: {str(e)}"
        }), 500

@app.route('/api/clear-cache', methods=['GET'])
def clear_cache():
    """Clear the cache directory to force regeneration of maps with improved state boundaries"""
    try:
        # Get the cache directory
        cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
        
        # Count files before deletion
        files_before = len([f for f in os.listdir(cache_dir) if os.path.isfile(os.path.join(cache_dir, f))])
        
        # Delete all HTML files in the cache directory
        for filename in os.listdir(cache_dir):
            if filename.endswith(".html"):
                file_path = os.path.join(cache_dir, filename)
                try:
                    os.remove(file_path)
                    logger.info(f"Deleted cached map: {file_path}")
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {e}")
        
        # Count files after deletion
        files_after = len([f for f in os.listdir(cache_dir) if os.path.isfile(os.path.join(cache_dir, f))])
        
        return jsonify({
            "success": True,
            "message": f"Cache cleared. Removed {files_before - files_after} files.",
            "filesRemoved": files_before - files_after
        })
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        return jsonify({
            "success": False,
            "message": f"Error clearing cache: {str(e)}"
        }), 500

@app.route('/api/health', methods=['GET', 'OPTIONS'])
def health_check():
    """A simple health check endpoint to test CORS and server status"""
    if request.method == 'OPTIONS':
        response = Response()
    else:
        response = jsonify({
            "status": "healthy",
            "message": "Backend server is operational",
            "timestamp": time.time(),
            "cors_enabled": True,
            "cache_dir_exists": os.path.exists(CACHE_DIR),
            "cached_maps": len([f for f in os.listdir(CACHE_DIR) if f.endswith('.html')]) if os.path.exists(CACHE_DIR) else 0
        })
    
    # Set CORS headers
    origin = request.headers.get('Origin', '')
    allowed_origins = ['https://sst-frontend-swart.vercel.app', 'http://localhost:3000', 'https://prototype-railway-production.up.railway.app']
    
    if origin in allowed_origins:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
    else:
        response.headers['Access-Control-Allow-Origin'] = '*'
        
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, Cache-Control, X-Requested-With'
    response.headers['Access-Control-Max-Age'] = '86400'
    
    return response

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
