from flask import Flask, render_template, request, jsonify
from pymodbus.client import ModbusTcpClient
from threading import Lock
import time

app = Flask(__name__)

# Configuration
PLC_IP = '192.168.10.21'  # Change to your PLC's IP address
PLC_PORT = 502        # Default Modbus port
REGISTER_COUNT = 10   # Number of registers (400001-400010)
MIN_VALUE = 0        # Minimum allowed value
MAX_VALUE = 5         # Maximum allowed value

# Modbus client setup with thread safety
client = ModbusTcpClient(PLC_IP, port=PLC_PORT)
client_lock = Lock()

def get_plc_registers():
    """Read current values from PLC registers"""
    try:
        with client_lock:
            if not client.connect():
                raise ConnectionError("Could not connect to PLC")
            
            # Read holding registers (Modbus address 0 is PLC address 400001)
            # Updated for pymodbus 3.x
            response = client.read_holding_registers(
                address=0, 
                count=REGISTER_COUNT,
                slave=1  # Change slave ID if needed
            )
            
            if response.isError():
                raise Exception(f"Modbus error: {response}")
            
            return response.registers
    except Exception as e:
        print(f"Error reading PLC registers: {e}")
        return None
    finally:
        client.close()

def write_plc_register(register, value):
    """Write a value to a specific PLC register"""
    try:
        with client_lock:
            if not client.connect():
                raise ConnectionError("Could not connect to PLC")
            
            # Validate register and value
            if register < 0 or register >= REGISTER_COUNT:
                raise ValueError("Invalid register address")
            
            if value < MIN_VALUE or value > MAX_VALUE:
                raise ValueError(f"Value must be between {MIN_VALUE} and {MAX_VALUE}")
            
            # Write to holding register (Modbus address 0 is PLC address 400001)
            # Updated for pymodbus 3.x
            response = client.write_register(
                address=register,
                value=value,
                slave=1  # Change slave ID if needed
            )
            
            if response.isError():
                raise Exception(f"Modbus error: {response}")
            
            return True
    except Exception as e:
        print(f"Error writing to PLC register: {e}")
        return False
    finally:
        client.close()

@app.route('/')
def index():
    """Render the main page with current register values"""
    registers = get_plc_registers() or [0] * REGISTER_COUNT
    return render_template('1to10.html', 
                         registers=registers,
                         min_value=MIN_VALUE,
                         max_value=MAX_VALUE)

@app.route('/update', methods=['POST'])
def update_register():
    """Handle AJAX request to update a register value"""
    try:
        register = int(request.form.get('register'))
        value = int(request.form.get('value'))
        
        if write_plc_register(register, value):
            return jsonify({'success': True, 'message': 'Register updated successfully'})
        else:
            return jsonify({'success': False, 'message': 'Failed to update register'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400

@app.route('/refresh')
def refresh_values():
    """Handle AJAX request to refresh register values"""
    registers = get_plc_registers()
    if registers is not None:
        return jsonify({'success': True, 'registers': registers})
    else:
        return jsonify({'success': False, 'message': 'Failed to read registers'}), 500

if __name__ == '__main__':
    app.run(debug=True)