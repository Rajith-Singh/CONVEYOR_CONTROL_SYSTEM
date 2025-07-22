from flask import Flask, render_template, request, redirect, url_for, flash
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException
import logging

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# PLC Configuration
PLC_IP = "192.168.10.21"
PLC_PORT = 502
PLC_MEMORY_ADDRESS = 0  # Holding Register 400001 is address 0 in Modbus (400001 - 400001 = 0)

# Configure logging
logging.basicConfig()
log = logging.getLogger()
log.setLevel(logging.INFO)

def get_plc_connection():
    """Create and return a Modbus TCP client connection"""
    try:
        client = ModbusTcpClient(PLC_IP, port=PLC_PORT)
        return client
    except Exception as e:
        log.error(f"Error creating Modbus client: {e}")
        return None

def read_plc_memory():
    """Read the current value from PLC memory"""
    client = get_plc_connection()
    if not client:
        return None
    
    try:
        client.connect()
        response = client.read_holding_registers(PLC_MEMORY_ADDRESS, 1)
        if response.isError():
            log.error(f"Modbus read error: {response}")
            return None
        return response.registers[0]
    except ModbusException as e:
        log.error(f"Modbus exception: {e}")
        return None
    finally:
        client.close()

def write_plc_memory(value):
    """Write a value to PLC memory"""
    client = get_plc_connection()
    if not client:
        return False
    
    try:
        client.connect()
        response = client.write_register(PLC_MEMORY_ADDRESS, value)
        if response.isError():
            log.error(f"Modbus write error: {response}")
            return False
        return True
    except ModbusException as e:
        log.error(f"Modbus exception: {e}")
        return False
    finally:
        client.close()

@app.route('/')
def index():
    current_value = read_plc_memory()
    if current_value is None:
        flash("Error reading from PLC", "error")
        current_value = "Error"
    return render_template('400001.html', current_value=current_value)

@app.route('/update', methods=['POST'])
def update():
    new_value = request.form.get('value')
    if new_value in ['0', '1']:
        success = write_plc_memory(int(new_value))
        if not success:
            flash("Failed to write to PLC", "error")
    else:
        flash("Invalid value submitted", "error")
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')