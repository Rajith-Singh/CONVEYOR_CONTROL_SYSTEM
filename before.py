from flask import Flask, render_template, request, jsonify, session, make_response
from pymodbus.client import ModbusTcpClient
from escpos.printer import Usb
import usb.core
import logging
from datetime import datetime, timedelta
import random
import string
import time
from threading import Lock, Thread
from reportlab.pdfgen import canvas
from io import BytesIO
import qrcode
import json
import os

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
PROXIMITY_SENSOR_1_ADDRESS = 400002
PROXIMITY_SENSOR_2_ADDRESS = 400003
QR_SCANNER_ADDRESS = 400004
ACCEPTED_BOXES_ADDRESS = 400005
PRODUCTION_COMPLETE_ADDRESS = 400006
BOX_STATUS_INDICATOR_ADDRESS = 400007

# Global variables
plc_lock = Lock()

# Ensure qr_codes directory exists
os.makedirs('static/qr_codes', exist_ok=True)

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

def generate_qr_code(production_data, box_index):
    """Generate a QR code with production details and unique ID"""
    # Generate unique ID with timestamp
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    unique_id = f"{production_data['biscuit_type'][:3]}-{production_data['brand'][:3]}-{timestamp}-{random_str}"
    
    # Create data dictionary
    qr_data = {
        "unique_id": unique_id,
        "biscuit_type": production_data['biscuit_type'],
        "brand": production_data['brand'],
        "production_type": production_data['production_type'],
        "production_time": datetime.now().isoformat(),
        "box_number": box_index + 1,
        "total_boxes": production_data['quantity']
    }
    
    # Convert to JSON string
    qr_data_str = json.dumps(qr_data, indent=2)
    
    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_data_str)
    qr.make(fit=True)
    
    # Create QR code image
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Save QR code image
    filename = f"static/qr_codes/{unique_id}.png"
    img.save(filename)
    
    return {
        "unique_id": unique_id,
        "qr_data": qr_data_str,
        "qr_image": filename
    }

def reset_plc_memory():
    """Reset all PLC memory locations to default values"""
    write_plc_memory(CONVEYOR_CONTROL_ADDRESS, 0)
    write_plc_memory(PROXIMITY_SENSOR_1_ADDRESS, 0)
    write_plc_memory(PROXIMITY_SENSOR_2_ADDRESS, 0)
    write_plc_memory(QR_SCANNER_ADDRESS, 0)
    write_plc_memory(PRODUCTION_COMPLETE_ADDRESS, 0)
    write_plc_memory(BOX_STATUS_INDICATOR_ADDRESS, 0)

def control_indicator_lights():
    """Control the indicator lights and auto-reset them"""
    while True:
        try:
            # Read current box status
            box_status = read_plc_memory(BOX_STATUS_INDICATOR_ADDRESS)
            
            # If status is not 0 (meaning we have a status to show), wait 1 second and reset
            if box_status != 0:
                time.sleep(1)
                write_plc_memory(BOX_STATUS_INDICATOR_ADDRESS, 0)
                
            time.sleep(0.1)  # Small delay to prevent excessive CPU usage
        except Exception as e:
            logger.error(f"Error in indicator light control: {str(e)}")
            time.sleep(1)

# Start the indicator light control thread
indicator_thread = Thread(target=control_indicator_lights, daemon=True)
indicator_thread.start()

def get_hp_printer():
    """Find and return HP DeskJet 2300 series printer"""
    try:
        # Your specific printer IDs from the error message
        dev = usb.core.find(idVendor=0x03F0, idProduct=0x3654)
        
        if dev is None:
            raise ValueError("Printer not found")
            
        # Release the device if it's already claimed
        try:
            dev.reset()
        except:
            pass
            
        # Initialize printer
        p = Usb(0x03F0, 0x3654, interface=0, in_ep=0x81, out_ep=0x01)
        return p
    except Exception as e:
        logger.error(f"Error initializing HP printer: {str(e)}")
        raise

def print_qr_code_to_hp_printer(qr_image_path):
    """Print QR code to HP DeskJet 2300 series printer (simulate if not connected)"""
    try:
        p = get_hp_printer()
        # Set larger image size (adjust as needed)
        p.image(qr_image_path, impl="bitImageColumn", high_density_vertical=True)
        p.cut()
        return True
    except Exception as e:
        logger.warning(f"Printer not connected or failed: {str(e)}. Simulating print and continuing.")
        # Simulate successful print so production can continue
        return True

@app.route('/')
def index():
    return render_template('bbb.html')

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

        # Generate QR codes for each box
        qr_codes = []
        for i in range(quantity):
            qr_data = {
                'biscuit_type': data['biscuit_type'],
                'brand': data['brand'],
                'production_type': data['production_type'],
                'quantity': quantity
            }
            qr_code = generate_qr_code(qr_data, i)
            qr_codes.append(qr_code)
        
        # Store production data in session
        session['production_data'] = {
            'biscuit_type': data['biscuit_type'],
            'brand': data['brand'],
            'production_type': data['production_type'],
            'quantity': quantity,
            'qr_codes': qr_codes,
            'current_index': 0,
            'accepted_boxes': 0,
            'rejected_boxes': 0,
            'start_time': datetime.now().isoformat(),
            'status': 'waiting_for_conveyor',
            'sensor_check_start': None,
            'waiting_for_proximity_1': True,
            'waiting_for_proximity_2': False,
            'waiting_for_qr': False
        }
        
        # Reset PLC memory (except accepted boxes counter)
        reset_plc_memory()
        write_plc_memory(ACCEPTED_BOXES_ADDRESS, 0)
        
        # Signal conveyor to start
        write_plc_memory(CONVEYOR_CONTROL_ADDRESS, 1)
        
        return jsonify({
            'message': 'Production started - waiting for conveyor',
            'first_qr_code': qr_codes[0],
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
        
        # Generate new QR code for the rejected box
        qr_data = {
            'biscuit_type': production['biscuit_type'],
            'brand': production['brand'],
            'production_type': production['production_type'],
            'quantity': production['quantity']
        }
        new_qr_code = generate_qr_code(qr_data, production['current_index'])
        production['qr_codes'][production['current_index']] = new_qr_code
        
        # Reset sensors
        write_plc_memory(PROXIMITY_SENSOR_1_ADDRESS, 0)
        write_plc_memory(PROXIMITY_SENSOR_2_ADDRESS, 0)
        write_plc_memory(QR_SCANNER_ADDRESS, 0)
        write_plc_memory(BOX_STATUS_INDICATOR_ADDRESS, 0)
        
        # Restart conveyor
        write_plc_memory(CONVEYOR_CONTROL_ADDRESS, 1)
        
        # Update production status
        production['status'] = 'running'
        production['waiting_for_proximity_1'] = True
        production['waiting_for_proximity_2'] = False
        production['waiting_for_qr'] = False
        production['sensor_check_start'] = None
        session['production_data'] = production
        
        return jsonify({
            'message': 'Conveyor restarted',
            'current_qr_code': production['qr_codes'][production['current_index']],
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
        
        if 'production_data' in session:
            production = session['production_data']
            production['status'] = 'running'
            production['waiting_for_proximity_1'] = True
            session['production_data'] = production
        
        return jsonify({'message': 'Conveyor started (simulated)'}), 200
    except Exception as e:
        logger.error(f"Error simulating conveyor: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/activate_proximity_1', methods=['POST'])
def activate_proximity_1():
    try:
        write_plc_memory(PROXIMITY_SENSOR_1_ADDRESS, 1)
        
        if 'production_data' in session:
            production = session['production_data']
            
            # Check if we're still processing the previous box
            if production['waiting_for_qr']:
                # Don't print QR code yet - we're still waiting for the previous box to be scanned
                return jsonify({
                    'message': 'Proximity sensor 1 activated (waiting for previous box to complete)',
                    'waiting_for_previous': True
                }), 200
            
            # Only print QR code if we're ready for a new box
            if production['waiting_for_proximity_1']:
                current_qr_code = production['qr_codes'][production['current_index']]
                if not print_qr_code_to_hp_printer(current_qr_code['qr_image']):
                    return jsonify({'error': 'Failed to print QR code'}), 500
                
                production['waiting_for_proximity_1'] = False
                production['waiting_for_proximity_2'] = True
                session['production_data'] = production
        
        return jsonify({
            'message': 'Proximity sensor 1 activated',
            'waiting_for_previous': False
        }), 200
    except Exception as e:
        logger.error(f"Error activating proximity sensor 1: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/activate_proximity_2', methods=['POST'])
def activate_proximity_2():
    try:
        write_plc_memory(PROXIMITY_SENSOR_2_ADDRESS, 1)
        
        if 'production_data' in session:
            production = session['production_data']
            if production['waiting_for_proximity_2']:
                production['waiting_for_proximity_2'] = False
                production['waiting_for_qr'] = True
                production['sensor_check_start'] = datetime.now().isoformat()
                session['production_data'] = production
        
        return jsonify({'message': 'Proximity sensor 2 activated'}), 200
    except Exception as e:
        logger.error(f"Error activating proximity sensor 2: {str(e)}")
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
        # Initialize default response
        response = {
            'status': 'not_started',
            'message': 'No active production',
            'has_queued_box': False
        }

        if 'production_data' not in session:
            return jsonify(response), 200

        production = session['production_data']
        conveyor_status = read_plc_memory(CONVEYOR_CONTROL_ADDRESS)

        # Validate production data structure
        required_fields = {
            'status': str,
            'current_index': int,
            'quantity': int,
            'accepted_boxes': int,
            'rejected_boxes': int,
            'waiting_for_proximity_1': bool,
            'waiting_for_proximity_2': bool,
            'waiting_for_qr': bool,
            'qr_codes': list
        }

        for field, field_type in required_fields.items():
            if field not in production or not isinstance(production[field], field_type):
                logger.error(f"Invalid production data: {field}")
                session.pop('production_data', None)
                return jsonify(response), 200

        # State machine for production flow
        if production['status'] == 'waiting_for_conveyor':
            if conveyor_status == 5:
                # Initialize new production run
                write_plc_memory(PROXIMITY_SENSOR_1_ADDRESS, 0)
                write_plc_memory(PROXIMITY_SENSOR_2_ADDRESS, 0)
                write_plc_memory(QR_SCANNER_ADDRESS, 0)
                write_plc_memory(BOX_STATUS_INDICATOR_ADDRESS, 0)

                production.update({
                    'status': 'running',
                    'waiting_for_proximity_1': True,
                    'queued_box': False,
                    'start_time': datetime.now().isoformat()
                })
                session['production_data'] = production

                response.update({
                    'status': 'running',
                    'current_qr_code': production['qr_codes'][production['current_index']],
                    'message': 'Conveyor started',
                    'waiting_for_proximity_1': True
                })
            else:
                response.update({
                    'status': 'waiting_for_conveyor',
                    'message': 'Waiting for conveyor start signal'
                })

        elif production['status'] == 'running':
            # Check production completion
            if production['current_index'] >= production['quantity']:
                production.update({
                    'status': 'completed',
                    'end_time': datetime.now().isoformat(),
                    'waiting_for_proximity_1': False,
                    'waiting_for_proximity_2': False,
                    'waiting_for_qr': False
                })
                write_plc_memory(PRODUCTION_COMPLETE_ADDRESS, 1)
                write_plc_memory(CONVEYOR_CONTROL_ADDRESS, 0)
                session['production_data'] = production

                response.update({
                    'status': 'completed',
                    'accepted_boxes': production['accepted_boxes'],
                    'rejected_boxes': production['rejected_boxes']
                })
                return jsonify(response), 200

            # Check sensor states
            proximity_1 = read_plc_memory(PROXIMITY_SENSOR_1_ADDRESS) == 1
            proximity_2 = read_plc_memory(PROXIMITY_SENSOR_2_ADDRESS) == 1
            qr_scanner = read_plc_memory(QR_SCANNER_ADDRESS) == 1

            # Handle queued box detection
            has_queued_box = proximity_1 and production['waiting_for_qr']
            if has_queued_box and not production.get('queued_box'):
                production['queued_box'] = True
                session['production_data'] = production

            # State transitions
            if production['waiting_for_proximity_1'] and proximity_1 and not production['waiting_for_qr']:
                # Print QR code for new box
                current_qr_code = production['qr_codes'][production['current_index']]
                if not print_qr_code_to_hp_printer(current_qr_code['qr_image']):
                    return jsonify({'error': 'QR print failed'}), 500

                production.update({
                    'waiting_for_proximity_1': False,
                    'waiting_for_proximity_2': True
                })
                session['production_data'] = production

            elif production['waiting_for_proximity_2'] and proximity_2:
                production.update({
                    'waiting_for_proximity_2': False,
                    'waiting_for_qr': True,
                    'sensor_check_start': datetime.now().isoformat()
                })
                session['production_data'] = production

            # Handle QR scanning timeout or success
            if production['waiting_for_qr']:
                elapsed = (datetime.now() - datetime.fromisoformat(
                    production['sensor_check_start'])).total_seconds()
                time_left = max(0, 5 - elapsed)

                if qr_scanner:
                    # Box accepted
                    accepted = read_plc_memory(ACCEPTED_BOXES_ADDRESS) + 1
                    write_plc_memory(ACCEPTED_BOXES_ADDRESS, accepted)
                    write_plc_memory(BOX_STATUS_INDICATOR_ADDRESS, 1)

                    production.update({
                        'accepted_boxes': accepted,
                        'current_index': production['current_index'] + 1,
                        'waiting_for_qr': False,
                        'queued_box': False
                    })

                    # Process queued box or wait for next
                    if production.get('queued_box'):
                        current_qr_code = production['qr_codes'][production['current_index']]
                        if not print_qr_code_to_hp_printer(current_qr_code['qr_image']):
                            return jsonify({'error': 'QR print failed'}), 500

                        production.update({
                            'waiting_for_proximity_1': False,
                            'waiting_for_proximity_2': True
                        })
                    else:
                        production['waiting_for_proximity_1'] = True

                    # Reset sensors
                    write_plc_memory(PROXIMITY_SENSOR_1_ADDRESS, 0)
                    write_plc_memory(PROXIMITY_SENSOR_2_ADDRESS, 0)
                    write_plc_memory(QR_SCANNER_ADDRESS, 0)

                    session['production_data'] = production
                    response.update({
                        'status': 'accepted',
                        'accepted_boxes': accepted,
                        'current_qr_code': production['qr_codes'][production['current_index']],
                        'remaining': production['quantity'] - production['current_index'],
                        'message': 'Box accepted'
                    })

                elif elapsed >= 5:
                    # Box rejected
                    production.update({
                        'rejected_boxes': production['rejected_boxes'] + 1,
                        'status': 'stopped',
                        'queued_box': False
                    })
                    write_plc_memory(BOX_STATUS_INDICATOR_ADDRESS, 2)
                    write_plc_memory(CONVEYOR_CONTROL_ADDRESS, 0)
                    session['production_data'] = production

                    response.update({
                        'status': 'stopped',
                        'rejected_boxes': production['rejected_boxes'],
                        'remaining': production['quantity'] - production['current_index'],
                        'can_restart': True
                    })
                else:
                    response.update({
                        'status': 'running',
                        'time_left': time_left,
                        'current_qr_code': production['qr_codes'][production['current_index']],
                        'message': 'Waiting for QR scan',
                        'has_queued_box': has_queued_box
                    })

            else:
                response.update({
                    'status': 'running',
                    'proximity_1_active': proximity_1,
                    'proximity_2_active': proximity_2,
                    'waiting_for_proximity_1': production['waiting_for_proximity_1'],
                    'waiting_for_proximity_2': production['waiting_for_proximity_2'],
                    'waiting_for_qr': production['waiting_for_qr'],
                    'message': 'Production in progress',
                    'has_queued_box': has_queued_box
                })

        elif production['status'] == 'completed':
            response.update({
                'status': 'completed',
                'accepted_boxes': production['accepted_boxes'],
                'rejected_boxes': production['rejected_boxes']
            })

        elif production['status'] == 'stopped':
            response.update({
                'status': 'stopped',
                'rejected_boxes': production['rejected_boxes'],
                'remaining': production['quantity'] - production['current_index'],
                'can_restart': True
            })

        return jsonify(response), 200

    except Exception as e:
        logger.error(f"Check status error: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'System error',
            'status': 'error'
        }), 500

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
                'proximity_1': read_plc_memory(PROXIMITY_SENSOR_1_ADDRESS),
                'proximity_2': read_plc_memory(PROXIMITY_SENSOR_2_ADDRESS),
                'scanner': read_plc_memory(QR_SCANNER_ADDRESS),
                'accepted_boxes': read_plc_memory(ACCEPTED_BOXES_ADDRESS),
                'production_complete': read_plc_memory(PRODUCTION_COMPLETE_ADDRESS),
                'box_status': read_plc_memory(BOX_STATUS_INDICATOR_ADDRESS)
            }
        }), 200
    except Exception as e:
        logger.error(f"Error getting production summary: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/generate_pdf', methods=['GET'])
def generate_pdf():
    try:
        if 'production_data' not in session:
            return jsonify({'error': 'No production data'}), 400
            
        production = session['production_data']
        start_time = datetime.fromisoformat(production['start_time'])
        end_time = datetime.fromisoformat(production['end_time']) if 'end_time' in production else datetime.now()
        duration = end_time - start_time
        success_rate = (production['accepted_boxes'] / production['quantity']) * 100 if production['quantity'] > 0 else 0
        
        # Create PDF
        buffer = BytesIO()
        p = canvas.Canvas(buffer)
        
        # PDF content
        p.setFont("Helvetica-Bold", 16)
        p.drawString(100, 800, "Biscuit Production Summary Report")
        
        p.setFont("Helvetica", 12)
        p.drawString(100, 770, f"Biscuit Type: {production['biscuit_type']}")
        p.drawString(100, 750, f"Brand: {production['brand']}")
        p.drawString(100, 730, f"Production Type: {production['production_type']}")
        p.drawString(100, 710, f"Quantity: {production['quantity']} boxes")
        
        p.drawString(100, 680, f"Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        p.drawString(100, 660, f"End Time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        p.drawString(100, 640, f"Duration: {str(duration)}")
        
        p.drawString(100, 610, f"Accepted Boxes: {production['accepted_boxes']}")
        p.drawString(100, 590, f"Rejected Boxes: {production['rejected_boxes']}")
        p.drawString(100, 570, f"Success Rate: {success_rate:.2f}%")
        
        p.drawString(100, 540, "Generated QR Codes:")
        y_position = 520
        for qr_code in production['qr_codes']:
            status = "Accepted" if production['qr_codes'].index(qr_code) < production['accepted_boxes'] else "Rejected"
            p.drawString(120, y_position, f"{qr_code['unique_id']} - {status}")
            y_position -= 20
            if y_position < 50:
                p.showPage()
                y_position = 800
        
        p.save()
        
        buffer.seek(0)
        response = make_response(buffer.getvalue())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = 'attachment; filename=production_summary.pdf'
        
        return response
        
    except Exception as e:
        logger.error(f"Error generating PDF: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/reset_production', methods=['POST'])
def reset_production():
    try:
        # Reset PLC memory
        reset_plc_memory()
        
        # Clear production data from session
        if 'production_data' in session:
            session.pop('production_data')
            
        return jsonify({'message': 'Production reset successfully'}), 200
    except Exception as e:
        logger.error(f"Error resetting production: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)