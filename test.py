from flask import Flask, render_template, request, jsonify
from pymodbus.client import ModbusTcpClient
import logging

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# PLC connection parameters
PLC_IP = "192.168.10.21"
PLC_PORT = 502
MEMORY_ADDRESS = 1  # The address we're working with

def get_plc_client():
    try:
        client = ModbusTcpClient(PLC_IP, port=PLC_PORT, timeout=2)
        return client
    except Exception as e:
        logger.error(f"Error creating Modbus client: {str(e)}")
        raise

def write_boolean_to_plc(value):
    client = None
    try:
        client = get_plc_client()
        if not client.connect():
            raise Exception("Failed to connect to PLC")
        
        logger.debug(f"Writing value {value} to address {MEMORY_ADDRESS}")
        # For pymodbus 3.x, write_coil takes address and value
        result = client.write_coil(MEMORY_ADDRESS - 1, value)
        if result.isError():
            raise Exception(f"PLC write error: {result}")
    except Exception as e:
        logger.error(f"Write error: {str(e)}")
        raise
    finally:
        if client:
            client.close()

def read_boolean_from_plc():
    client = None
    try:
        client = get_plc_client()
        if not client.connect():
            raise Exception("Failed to connect to PLC")
        
        logger.debug(f"Reading from address {MEMORY_ADDRESS}")
        # For pymodbus 3.x, read_holding_registers takes address and count
        result = client.read_holding_registers(MEMORY_ADDRESS - 1, count=1)
        if result.isError():
            raise Exception(f"PLC read error: {result}")
        
        return result.bits[0]
    except Exception as e:
        logger.error(f"Read error: {str(e)}")
        raise
    finally:
        if client:
            client.close()

@app.route('/')
def index():
    return render_template('test.html')

@app.route('/write_plc', methods=['POST'])
def write_plc():
    data = request.get_json()
    value = data.get('value')

    if value is None:
        return jsonify({'error': 'Value is required'}), 400

    try:
        write_boolean_to_plc(value)
        return jsonify({'message': 'Success'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/read_plc', methods=['GET'])
def read_plc():
    try:
        value = read_boolean_from_plc()
        return jsonify({'value': value}), 200
    except Exception as e:
        logger.exception("Error reading from PLC")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)