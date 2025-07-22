from flask import Flask, render_template, request, jsonify, session
from pymodbus.client import ModbusTcpClient
import logging
from datetime import datetime, timedelta
import random
import string
import time
from threading import Lock

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# PLC connection parameters
PLC_IP = "192.168.10.21"
PLC_PORT = 502

# Memory addresses
CONVEYOR_CONTROL_ADDRESS = 400001
PROXIMITY_SENSOR_ADDRESS = 400002
QR_SCANNER_ADDRESS = 400003
ACCEPTED_BOXES_ADDRESS = 400005

# Global variables
plc_lock = Lock()

def get_plc_client():
    try:
        client = ModbusTcpClient(PLC_IP, port=PLC_PORT, timeout=2)
        return client
    except Exception as e:
        logger.error(f"Error creating Modbus client: {str(e)}")
        raise

def read_plc_memory(address):
    with plc_lock:
        client = None
        try:
            client = get_plc_client()
            if not client.connect():
                raise Exception("Failed to connect to PLC")
            
            result = client.read_holding_registers(address - 400001, count=1)
            if result.isError():
                raise Exception(f"PLC read error: {result}")
            
            return result.registers[0]
        except Exception as e:
            logger.error(f"Read error: {str(e)}")
            raise
        finally:
            if client:
                client.close()

def write_plc_memory(address, value):
    with plc_lock:
        client = None
        try:
            client = get_plc_client()
            if not client.connect():
                raise Exception("Failed to connect to PLC")
            
            result = client.write_register(address - 400001, value)
            if result.isError():
                raise Exception(f"PLC write error: {result}")
        except Exception as e:
            logger.error(f"Write error: {str(e)}")
            raise
        finally:
            if client:
                client.close()

def generate_unique_code(biscuit_type, brand):
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"{biscuit_type[:3]}-{brand[:3]}-{timestamp}-{random_str}"

def reset_plc_memory():
    """Reset all PLC memory locations to default values"""
    write_plc_memory(CONVEYOR_CONTROL_ADDRESS, 0)
    write_plc_memory(PROXIMITY_SENSOR_ADDRESS, 0)
    write_plc_memory(QR_SCANNER_ADDRESS, 0)
    # Don't reset accepted boxes counter here - we'll do it when starting new production

@app.route('/')
def index():
    return render_template('pro.html')

@app.route('/start_production', methods=['POST'])
def start_production():
    data = request.get_json()
    
    # Validate data
    required_fields = ['biscuit_type', 'brand', 'production_type', 'custom_quantity']
    if not all(field in data for field in required_fields):
        return jsonify({'error': 'Missing required fields'}), 400
    
    try:
        # Determine production quantity
        if data['production_type'] == 'P1':
            quantity = 10
        elif data['production_type'] == 'P2':
            quantity = 20
        else:
            quantity = int(data['custom_quantity'])
            if quantity <= 0:
                return jsonify({'error': 'Quantity must be positive'}), 400

        # Generate unique codes
        codes = [generate_unique_code(data['biscuit_type'], data['brand']) for _ in range(quantity)]
        
        # Store production data in session
        session['production_data'] = {
            'biscuit_type': data['biscuit_type'],
            'brand': data['brand'],
            'production_type': data['production_type'],
            'quantity': quantity,
            'codes': codes,
            'current_index': 0,
            'accepted_boxes': 0,
            'rejected_boxes': 0,
            'start_time': datetime.now().isoformat(),
            'status': 'waiting_for_conveyor',
            'sensor_check_start': None
        }
        
        # Reset accepted boxes counter
        write_plc_memory(ACCEPTED_BOXES_ADDRESS, 0)
        
        # Signal conveyor to start
        write_plc_memory(CONVEYOR_CONTROL_ADDRESS, 1)
        
        return jsonify({
            'message': 'Production started - waiting for conveyor',
            'first_code': codes[0],
            'total_quantity': quantity
        }), 200
        
    except Exception as e:
        logger.error(f"Error starting production: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/restart_conveyor', methods=['POST'])
def restart_conveyor():
    try:
        if 'production_data' not in session:
            return jsonify({'error': 'No production to restart'}), 400
            
        production = session['production_data']
        
        # Check if production was stopped
        if production['status'] != 'stopped':
            return jsonify({'error': 'Production not in stopped state'}), 400
        
        # Generate new code for the rejected box
        production['codes'][production['current_index']] = generate_unique_code(
            production['biscuit_type'], production['brand'])
        
        # Restart conveyor
        write_plc_memory(CONVEYOR_CONTROL_ADDRESS, 1)
        
        # Update production status
        production['status'] = 'waiting_for_conveyor'
        production['sensor_check_start'] = None
        session['production_data'] = production
        
        return jsonify({
            'message': 'Conveyor restarted',
            'current_code': production['codes'][production['current_index']],
            'remaining': production['quantity'] - production['current_index']
        }), 200
        
    except Exception as e:
        logger.error(f"Error restarting conveyor: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/simulate_conveyor_start', methods=['POST'])
def simulate_conveyor_start():
    try:
        # Simulate conveyor sending 5 to memory address 400001
        write_plc_memory(CONVEYOR_CONTROL_ADDRESS, 5)
        return jsonify({'message': 'Conveyor started (simulated)'}), 200
    except Exception as e:
        logger.error(f"Error simulating conveyor: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/activate_proximity', methods=['POST'])
def activate_proximity():
    try:
        write_plc_memory(PROXIMITY_SENSOR_ADDRESS, 1)
        return jsonify({'message': 'Proximity sensor activated'}), 200
    except Exception as e:
        logger.error(f"Error activating proximity sensor: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/activate_scanner', methods=['POST'])
def activate_scanner():
    try:
        write_plc_memory(QR_SCANNER_ADDRESS, 1)
        return jsonify({'message': 'QR scanner activated'}), 200
    except Exception as e:
        logger.error(f"Error activating QR scanner: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/check_production_status', methods=['GET'])
def check_production_status():
    try:
        if 'production_data' not in session:
            return jsonify({'error': 'No active production'}), 400
            
        production = session['production_data']
        conveyor_status = read_plc_memory(CONVEYOR_CONTROL_ADDRESS)
        
        # Check current production state
        if production['status'] == 'waiting_for_conveyor':
            if conveyor_status == 5:
                # Conveyor has started - reset sensors and begin production
                write_plc_memory(PROXIMITY_SENSOR_ADDRESS, 0)
                write_plc_memory(QR_SCANNER_ADDRESS, 0)
                
                production['status'] = 'running'
                production['sensor_check_start'] = datetime.now().isoformat()
                session['production_data'] = production
                return jsonify({
                    'status': 'running',
                    'current_code': production['codes'][production['current_index']],
                    'message': 'Conveyor started - production running'
                }), 200
            else:
                return jsonify({
                    'status': 'waiting_for_conveyor',
                    'message': 'Waiting for conveyor to start'
                }), 200
                
        elif production['status'] == 'running':
            # Check if production is complete
            if production['current_index'] >= production['quantity']:
                production['status'] = 'completed'
                production['end_time'] = datetime.now().isoformat()
                session['production_data'] = production
                
                # Stop conveyor and reset PLC
                write_plc_memory(CONVEYOR_CONTROL_ADDRESS, 0)
                reset_plc_memory()
                
                return jsonify({
                    'status': 'completed',
                    'accepted_boxes': production['accepted_boxes'],
                    'rejected_boxes': production['rejected_boxes']
                }), 200
            
            # Check sensor status
            proximity_active = read_plc_memory(PROXIMITY_SENSOR_ADDRESS) == 1
            scanner_active = read_plc_memory(QR_SCANNER_ADDRESS) == 1
            
            if proximity_active and scanner_active:
                # Box accepted
                accepted_boxes = read_plc_memory(ACCEPTED_BOXES_ADDRESS) + 1
                write_plc_memory(ACCEPTED_BOXES_ADDRESS, accepted_boxes)
                
                # Update production data
                production['accepted_boxes'] = accepted_boxes
                production['current_index'] += 1
                production['sensor_check_start'] = None
                session['production_data'] = production
                
                # Reset sensors and prepare for next box
                write_plc_memory(PROXIMITY_SENSOR_ADDRESS, 0)
                write_plc_memory(QR_SCANNER_ADDRESS, 0)
                
                # Check if production is complete
                if production['current_index'] >= production['quantity']:
                    production['status'] = 'completed'
                    production['end_time'] = datetime.now().isoformat()
                    session['production_data'] = production
                    
                    # Stop conveyor and reset PLC
                    write_plc_memory(CONVEYOR_CONTROL_ADDRESS, 0)
                    reset_plc_memory()
                    
                    return jsonify({
                        'status': 'completed',
                        'accepted_boxes': accepted_boxes,
                        'rejected_boxes': production['rejected_boxes']
                    }), 200
                
                # Start timer for next box
                production['sensor_check_start'] = datetime.now().isoformat()
                session['production_data'] = production
                
                return jsonify({
                    'status': 'accepted',
                    'accepted_boxes': accepted_boxes,
                    'current_code': production['codes'][production['current_index']],
                    'remaining': production['quantity'] - production['current_index'],
                    'message': 'Box accepted - next box in progress'
                }), 200
            else:
                # Check if 5 seconds have passed
                check_start = datetime.fromisoformat(production['sensor_check_start'])
                if (datetime.now() - check_start).total_seconds() >= 5:
                    # Box rejected
                    production['rejected_boxes'] += 1
                    production['status'] = 'stopped'
                    session['production_data'] = production
                    
                    # Stop conveyor and reset sensors
                    write_plc_memory(CONVEYOR_CONTROL_ADDRESS, 0)
                    write_plc_memory(PROXIMITY_SENSOR_ADDRESS, 0)
                    write_plc_memory(QR_SCANNER_ADDRESS, 0)
                    
                    return jsonify({
                        'status': 'stopped',
                        'rejected_boxes': production['rejected_boxes'],
                        'remaining': production['quantity'] - production['current_index'],
                        'can_restart': True
                    }), 200
                else:
                    return jsonify({
                        'status': 'running',
                        'time_left': 5 - (datetime.now() - check_start).total_seconds(),
                        'current_code': production['codes'][production['current_index']],
                        'message': 'Waiting for sensors to activate'
                    }), 200
    except Exception as e:
        logger.error(f"Error checking production status: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/production_summary', methods=['GET'])
def production_summary():
    try:
        if 'production_data' not in session:
            return jsonify({'error': 'No production data'}), 400
            
        production = session['production_data']
        return jsonify({
            'production_data': production,
            'plc_status': {
                'conveyor': read_plc_memory(CONVEYOR_CONTROL_ADDRESS),
                'proximity': read_plc_memory(PROXIMITY_SENSOR_ADDRESS),
                'scanner': read_plc_memory(QR_SCANNER_ADDRESS),
                'accepted_boxes': read_plc_memory(ACCEPTED_BOXES_ADDRESS)
            }
        }), 200
    except Exception as e:
        logger.error(f"Error getting production summary: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)