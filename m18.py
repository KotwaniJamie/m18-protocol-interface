import code
import datetime
import struct
import time
from argparse import ArgumentParser
from re import findall

from requests import post
from serial import Serial
from serial.tools import list_ports

data_matrix = [
    [0x00, 0x00, 0x02],
    [0x00, 0x02, 0x02],
    [0x00, 0x04, 0x05],
    [0x00, 0x0D, 0x04],
    [0x00, 0x11, 0x04],
    [0x00, 0x15, 0x04],
    [0x00, 0x19, 0x04],
    [0x00, 0x23, 0x14],
    [0x00, 0x37, 0x04],
    [0x00, 0x69, 0x02],
    [0x00, 0x7B, 0x01],
    [0x40, 0x00, 0x04],
    [0x40, 0x0A, 0x0A],
    [0x40, 0x14, 0x02],
    [0x40, 0x16, 0x02],
    [0x40, 0x19, 0x02], # New - 20 Aug
    [0x40, 0x1B, 0x02], 
    [0x40, 0x1D, 0x02], # New - 20 Aug
    [0x40, 0x1F, 0x02], # New - 20 Aug
    [0x60, 0x00, 0x02],
    [0x60, 0x02, 0x02],
    [0x60, 0x04, 0x04],
    [0x60, 0x08, 0x04],
    [0x60, 0x0C, 0x02], # New - 20 Aug
    [0x90, 0x00, 0x3A], # 338 byte RAM chunk. Grab every 58 bytes
    [0x90, 0x3A, 0x3A], # RAM chunk
    [0x90, 0x74, 0x3A], # RAM chunk
    [0x90, 0xAE, 0x3A], # RAM chunk
    [0x90, 0xE8, 0x3A], # RAM chunk
    [0x91, 0x22, 0x30], # RAM chunk. Grab last 48 bytes
    [0x91, 0x52, 0x00], # Always empty. Maybe marks end of RAM chunk
    [0xA0, 0x00, 0x06]
]


# label, addr, len, type 
#   uint - unsigned integer
#   date - UNIX time (seconds from 1 Jan 1970)
#   ascii - ascii string
#   sn - serial number (2 bytes battery type, 3 bytes serial)
#   adc_t - analog-to-digital converter temperature (mV of thermistor)
#   dec_t - decimal temperature (byte_1 + byte_2/255)
#   cell_v - cell voltages (1: 3568, 2: 3567, 3:3570, etc)
data_id = [
    [0x0000, 2,  "uint",  "Cell type"],                       # 0
    [0x0002, 2,  "uint",  "Unknown (always 0)"], 
    [0x0004, 5,  "sn",    "Capacity & Serial number (?)"], 
    [0x000D, 4,  "uint",  "Unknown (4th code?)"], 
    [0x0011, 4,  "date",  "Manufacture date"], 
    [0x0015, 4,  "date",  "Date of first charge (Forge)"], 
    [0x0019, 4,  "date",  "Date of last charge (Forge)"], 
    [0x0023, 20, "ascii", "Note (ascii string)"], 
    [0x0037, 4,  "date",  "Current date"], 
    [0x0069, 2,  "uint",  "Unknown (always 2)"]	, 
    [0x007B, 1,  "uint",  "Unknown (always 0)"],              # 10
    [0x4000, 4,  "uint",  "Unknown (Forge)"], 
    [0x400A, 10, "cell_v","Cell voltages (mV)"], 
    [0x4014, 2,  "adc_t", "Temperature (C) (non-Forge)"], 
    [0x4016, 2,  "uint",  "Unknown (Forge)"], 
    [0x4019, 2,  "uint",  "Unknown (Forge)"], 
    [0x401B, 2,  "uint",  "Unknown (Forge)"], 
    [0x401D, 2,  "uint",  "Unknown (Forge)"], 
    [0x401F, 2,  "dec_t", "Temperature (C) (Forge)"], 
    [0x6000, 2,  "uint",  "Unknown (Forge)"], 
    [0x6002, 2,  "uint",  "Unknown (Forge)"],                 # 20
    [0x6004, 4,  "uint",  "Unknown (Forge)"], 
    [0x6008, 4,  "uint",  "Unknown (Forge)"], 
    [0x600C, 2,  "uint",  "Unknown (Forge)"], 
    [0x9000, 4,  "date",  "Date of first charge (rounded)"], 
    [0x9004, 4,  "date",  "Date of last tool use (rounded)"], 
    [0x9008, 4,  "date",  "Date of last charge (rounded)"], 
    [0x900C, 4,  "date",  "Unknown date (often zero)"], 
    [0x9010, 2,  "uint",  "Days since first charge"], 
    [0x9012, 4,  "uint",  "Total discharge (amp-sec)"], 
    [0x9016, 4,  "uint",  "Total discharge (watt-sec or joules)"],    #30 
    [0x901A, 4,  "uint",  "Total charge count"], 
    [0x901E, 2,  "uint",  "Dumb charge count (J2>7.1V for >=0.48s)"], 
    [0x9020, 2,  "uint",  "Redlink (UART) charge count"], 
    [0x9022, 2,  "uint",  "Completed charge count (?)"], 
    [0x9024, 4,  "hhmmss","Total charging time (HH:MM:SS)"], 
    [0x9028, 4,  "hhmmss","Time on charger whilst full (HH:MM:SS)"], 
    [0x902C, 2,  "uint",  "Unknown (another low-voltage charge counter?)"], 
    [0x902E, 2,  "uint",  "Charge started with a cell < 2.5V"], 
    [0x9030, 2,  "uint",  "Discharge to empty"], 
    [0x9032, 2,  "uint",  "Num. overheat on tool (must be > 10A)"],   #40
    [0x9034, 2,  "uint",  "Overcurrent?"], 
    [0x9036, 2,  "uint",  "Low voltage events)"], 
    [0x9038, 2,  "uint",  "Low-voltage bounce? (4 flashing LEDs)"], 
    [0x903A, 2,  "uint",  "Discharge @ 10-20A (seconds)"], 
    [0x903C, 2,  "uint",  "          @ 20-30A (could be watts)"], 
    [0x903E, 2,  "uint",  "          @ 30-40A      "], 
    [0x9040, 2,  "uint",  "          @ 40-50A      "], 
    [0x9042, 2,  "uint",  "          @ 50-60A      "], 
    [0x9044, 2,  "uint",  "          @ 60-70A      "], 
    [0x9046, 2,  "uint",  "          @ 70-80A      "],    #50
    [0x9048, 2,  "uint",  "          @ 80-90A      "], 
    [0x904A, 2,  "uint",  "          @ 90-100A     "], 
    [0x904C, 2,  "uint",  "          @ 100-110A    "], 
    [0x904E, 2,  "uint",  "          @ 110-120A    "], 
    [0x9050, 2,  "uint",  "          @ 120-130A    "], 
    [0x9052, 2,  "uint",  "          @ 130-140A    "], 
    [0x9054, 2,  "uint",  "          @ 140-150A    "], 
    [0x9056, 2,  "uint",  "          @ 150-160A    "], 
    [0x9058, 2,  "uint",  "          @ 160-170A    "], 
    [0x905A, 2,  "uint",  "          @ 170-180A    "],    #60
    [0x905C, 2,  "uint",  "          @ 180-190A    "], 
    [0x905E, 2,  "uint",  "          @ 190-200A    "], 
    [0x9060, 2,  "uint",  "          @ 200-210A    "], 
    [0x9062, 2,  "uint",  "Discharge @ 5-10A (seconds)"], 
    [0x9064, 2,  "uint",  "          @ 10-15A (could be watts)"], 
    [0x9066, 2,  "uint",  "          @ 15-20A (histo not well understood yet)"], 
    [0x9068, 2,  "uint",  "          @ 20-25A      "], 
    [0x906A, 2,  "uint",  "          @ 25-30A      "], 
    [0x906C, 2,  "uint",  "          @ 30-35A      "], 
    [0x906E, 2,  "uint",  "          @ 35-40A      "],    #70
    [0x9070, 2,  "uint",  "          @ 40-45A      "], 
    [0x9072, 2,  "uint",  "          @ 45-50A      "], 
    [0x9074, 2,  "uint",  "          @ 50-55A      "], 
    [0x9076, 2,  "uint",  "          @ 55-60A      "], 
    [0x9078, 2,  "uint",  "          @ 60-65A      "], 
    [0x907A, 2,  "uint",  "          @ 65-70A      "], 
    [0x907C, 2,  "uint",  "          @ 70-65A      "], 
    [0x907E, 2,  "uint",  "          @ 75-80A      "], 
    [0x9080, 2,  "uint",  "          @ 80-85A      "], 
    [0x9082, 2,  "uint",  "          @ 85-90A      "],    #80
    [0x9084, 2,  "uint",  "          @ 90-95A      "], 
    [0x9086, 2,  "uint",  "          @ 95-100A     "], 
    [0x9088, 2,  "uint",  "          @ 100-105A    "], 
    [0x908A, 2,  "uint",  "          @ 105-110A    "], 
    [0x908C, 2,  "uint",  "          @ 110-115A    "], 
    [0x908E, 2,  "uint",  "          @ 115-120A    "], 
    [0x9090, 2,  "uint",  "          @ 120-125A    "], 
    [0x9092, 2,  "uint",  "          @ 125-130A    "], 
    [0x9094, 2,  "uint",  "          @ 130-135A    "], 
    [0x9096, 2,  "uint",  "          @ 135-140A    "],    #90
    [0x9098, 2,  "uint",  "          @ 140-145A    "], 
    [0x909A, 2,  "uint",  "          @ 145-150A    "], 
    [0x909C, 2,  "uint",  "          @ 150-155A    "], 
    [0x909E, 2,  "uint",  "          @ 155-160A    "], 
    [0x90A0, 2,  "uint",  "          @ 160-165A    "], 
    [0x90A2, 2,  "uint",  "          @ 165-170A    "], 
    [0x90A4, 2,  "uint",  "          @ 170-175A    "], 
    [0x90A6, 2,  "uint",  "          @ 175-180A    "], 
    [0x90A8, 2,  "uint",  "          @ 180-185A    "], 
    [0x90AA, 2,  "uint",  "          @ 185-190A    "],    #100
    [0x90AC, 2,  "uint",  "          @ 190-195A    "], 
    [0x90AE, 2,  "uint",  "          @ 195-200A    "], 
    [0x90B0, 2,  "uint",  "          @ 200A+       "], 
    [0x90B2, 2,  "uint",  "Charge started < 17V"], 
    [0x90B4, 2,  "uint",  "Charge started 17-18V"], 
    [0x90B6, 2,  "uint",  "Charge started 18-19V"], 
    [0x90B8, 2,  "uint",  "Charge started 19-20V"], 
    [0x90BA, 2,  "uint",  "Charge started 20V+"], 
    [0x90BC, 2,  "uint",  "Charge ended < 17V"], 
    [0x90BE, 2,  "uint",  "Charge ended 17-18V"],         #110
    [0x90C0, 2,  "uint",  "Charge ended 18-19V"],  
    [0x90C2, 2,  "uint",  "Charge ended 19-20V"],  
    [0x90C4, 2,  "uint",  "Charge ended 20V+"],
    [0x90C6, 2,  "uint",  "Charge start temp -30C to -20C"], 
    [0x90C8, 2,  "uint",  "Charge start temp -20C to -10C"], 
    [0x90CA, 2,  "uint",  "Charge start temp -10C to 0C"],  
    [0x90CC, 2,  "uint",  "Charge start temp 0C to +10C"],  
    [0x90CE, 2,  "uint",  "Charge start temp +10C to +20C"],  
    [0x90D0, 2,  "uint",  "Charge start temp +20C to +30C"], 
    [0x90D2, 2,  "uint",  "Charge start temp +30C to +40C"],      #120
    [0x90D4, 2,  "uint",  "Charge start temp +40C to +50C"],  
    [0x90D6, 2,  "uint",  "Charge start temp +50C to +60C"], 
    [0x90D8, 2,  "uint",  "Charge start temp +60C to +70C"],  
    [0x90DA, 2,  "uint", "Charge start temp +70C to +80C"],  
    [0x90DC, 2,  "uint",  "Charge start temp +80C and over"], 
    [0x90DE, 2,  "uint",  "Charge end temp -30C to -20C"], 
    [0x90E0, 2,  "uint",  "Charge end temp -20C to -10C"], 
    [0x90E2, 2,  "uint",  "Charge end temp -10C to 0C"],  
    [0x90E4, 2,  "uint",  "Charge end temp 0C to +10C"],  
    [0x90E6, 2,  "uint",  "Charge end temp +10C to +20C"],         #130
    [0x90E8, 2,  "uint",  "Charge end temp +30C to +30C"],
    [0x90EA, 2,  "uint",  "Charge end temp +30C to +40C"],
    [0x90EC, 2,  "uint",  "Charge end temp +40C to +50C"],
    [0x90EE, 2,  "uint",  "Charge end temp +50C to +60C"],
    [0x90F0, 2,  "uint",  "Charge end temp +60C to +70C"],
    [0x90F2, 2,  "uint",  "Charge end temp +70C to +80C"],
    [0x90F4, 2,  "uint",  "Charge end temp +80C and over"], 
    [0x90F6, 2,  "uint",  "Dumb charge time (00:00-14:33)"], 
    [0x90F8, 2,  "uint",  "Dumb charge time (14:34-29:07)"], 
    [0x90FA, 2,  "uint",  "Dumb charge time (29:08-43:41)"],  #140
    [0x90FC, 2,  "uint",  "Dumb charge time (43:42-58:15)"], 
    [0x90FE, 2,  "uint",  "Dumb charge time (58:16-1:12:49)"], 
    [0x9100, 2,  "uint",  "Dumb charge time (1:12:50-1:27:23)"], 
    [0x9102, 2,  "uint",  "Dumb charge time (1:27:24-1:41:57)"], 
    [0x9104, 2,  "uint",  "Dumb charge time (1:41:58-1:56:31)"], 
    [0x9106, 2,  "uint",  "Dumb charge time (1:56:32-2:11:05)"], 
    [0x9108, 2,  "uint",  "Dumb charge time (2:11:06-2:25:39)"], 
    [0x910A, 2,  "uint",  "Dumb charge time (2:25:40-2:40:13)"], 
    [0x910C, 2,  "uint",  "Dumb charge time (2:40:14-2:54:47)"], 
    [0x910E, 2,  "uint",  "Dumb charge time (2:54:48-3:09:21)"],             #150
    [0x9110, 2,  "uint",  "Dumb charge time (3:09:22-3:23:55)"], 
    [0x9112, 2,  "uint",  "Redlink charge time (00:00-17:03)"], 
    [0x9114, 2,  "uint",  "Redlink charge time (17:04-34:07)"], 
    [0x9116, 2,  "uint",  "Redlink charge time (34:08-51:11)"], 
    [0x9118, 2,  "uint",  "Redlink charge time (51:12-1:08:15)"], 
    [0x911A, 2,  "uint",  "Redlink charge time (1:08:16-1:25:19)"], 
    [0x911C, 2,  "uint",  "Redlink charge time (1:25:20-1:42:23)"], 
    [0x911E, 2,  "uint",  "Redlink charge time (1:42:24-1:59:27)"], 
    [0x9120, 2,  "uint",  "Redlink charge time (1:59:28-2:16:31)"], 
    [0x9122, 2,  "uint",  "Redlink charge time (2:16:32-2:33:35)"],   #160 
    [0x9124, 2,  "uint",  "Redlink charge time (2:33:36-2:50:39)"], 
    [0x9126, 2,  "uint",  "Redlink charge time (2:50:40-3:07:43)"], 
    [0x9128, 2,  "uint",  "Redlink charge time (3:07:44-3:24:47)"], 
    [0x912A, 2,  "uint",  "Redlink charge time (3:24:48-3:41:51)"], 
    [0x912C, 2,  "uint",  "Redlink charge time (3:41:52-3:58:55)"], 
    [0x912E, 2,  "uint",  "Completed charge (?)"], 
    [0x9130, 2,  "uint",  "Unknown"], 
    [0x9132, 2,  "uint",  "Unknown"], 
    [0x9134, 2,  "uint",  "Unknown"], 
    [0x9136, 2,  "uint",  "Unknown"],       #170
    [0x9138, 2,  "uint",  "Unknown"], 
    [0x913A, 2,  "uint",  "Unknown"], 
    [0x913C, 2,  "uint",  "Unknown"], 
    [0x913E, 2,  "uint",  "Unknown"], 
    [0x9140, 2,  "uint",  "Unknown"], 
    [0x9142, 2,  "uint",  "Unknown histogram (temperature?)"], 
    [0x9144, 2,  "uint",  "Unknown histogram"], 
    [0x9146, 2,  "uint",  "Unknown histogram"], 
    [0x9148, 2,  "uint",  "Unknown histogram"], 
    [0x914A, 2,  "uint",  "Unknown histogram"],       # 180
    [0x914C, 2,  "uint",  "Unknown histogram"], 
    [0x914E, 2,  "uint",  "Unknown histogram"], 
    [0x9150, 2,  "uint",  "Unknown histogram"],       #183
]






# Battery type code -> [capacity in Ah, human description].
# Capacity is 0 where the type is not known, which is what stops
# discharge cycles being derived from it.
bat_lookup = {
    "36": [1.5, "1.5Ah CP (5s1p 18650)"],
    "37": [2, "2Ah CP (5s1p 18650)"],
    "38": [3, "3Ah XC (5s2p 18650)"],
    "39": [4, "4Ah XC (5s2p 18650)"],
    "40": [5, "5Ah XC (5s2p 18650) (<= Dec 2018)"],
    "165": [5, "5Ah XC (5s2p 18650) (Aug 2019 - Jun 2021)"],
    "306": [5, "5Ah XC (5s2p 18650) (Feb 2021 - Jul 2023)"],                
    "424": [5, "5Ah XC (5s2p 18650) (>= Sep 2023)"],
    "46": [6, "6Ah XC (5s2p 18650)"],
    "47": [9, "9Ah HD (5s3p 18650)"],                
    "104": [3, "3Ah HO (5s1p 21700)"],
    "150": [6, "5.5Ah HO (5s2p 21700) (EU only)"],
    "106": [6, "6Ah HO (5s2p 21700)"],
    "107": [8, "8Ah HO (5s2p 21700)"],
    "108": [12, "12Ah HO (5s3p 21700)"],
    "383": [8, "8Ah Forge (5s2p 21700 tabless)"],
    "384": [12, "12Ah Forge (5s3p 21700 tabless)"]
}


# Registers health() reports on, keyed by the field they populate.
# health() used to index the array returned by read_id() with hand-counted
# offsets, so inserting or reordering a register silently shifted every
# field after it. Looking up by register id removes that coupling.
HEALTH_REGISTERS = {
    "manufacture_date":        4,
    "days_since_first_charge": 28,
    "last_tool_use_date":      25,
    "last_charge_date":        26,
    "cell_voltages_mv":        12,
    "temperature_c":           13,
    "temperature_c_forge":     18,
    "total_discharge_as":      29,
    "discharged_to_empty":     39,
    "times_overheated":        40,
    "overcurrent_events":      41,
    "low_voltage_events":      42,
    "low_voltage_bounce":      43,
    "charge_count_redlink":    33,
    "charge_count_dumb":       32,
    "charge_count_total":      31,
    "total_charge_time":       35,
    "time_idle_on_charger":    36,
    "low_voltage_charges":     38,
    "battery_date":            8,
    "serial_raw":              2,
}

# Discharge histogram: 10-20A, 20-30A, ... 190-200A, then everything above.
HEALTH_BUCKET_REGISTERS = tuple(range(44, 64))
HEALTH_BUCKET_RANGES = tuple(
    [f"{(i + 1) * 10}-{(i + 2) * 10}A" for i in range(19)] + ["> 200A"]
)

# A pack with no data answers with an all-ones word rather than an error,
# so these have to be caught before anything derived from them is trusted.
# See issue #28: they otherwise surface as 65535, negative day counts, and
# charge times of 1193046:28:15 (that is 2**32 seconds).
UINT_SENTINELS = (0xFFFF, 0xFFFFFFFF)
HHMMSS_SENTINEL = "1193046:28:15"


def health_field(value, valid=None):
    """
    One measurement plus whether it can be trusted.

    Pass `valid` explicitly where validity depends on something other than
    the value itself; otherwise it is inferred from the sentinels above.
    The value is always passed through, valid or not, so a caller that
    wants to show the raw reading still can.
    """
    if valid is None:
        valid = (value is not None
                 and value not in UINT_SENTINELS
                 and value != HHMMSS_SENTINEL)
    return {"value": value, "valid": valid}


def date_valid(value):
    """
    A date register that was never written reads back as epoch zero, which
    is a missing value rather than a real date in 1970.
    """
    return value is not None and value.timestamp() != 0


def days_between(later, earlier):
    """Whole days between two register dates, or None if either is missing."""
    if later is None or earlier is None:
        return None
    return (later - earlier).days


def print_debug_bytes(data):
    data_print = " ".join(f"{byte:02X}" for byte in data)
    print(f"DEBUG: ", data_print)

class M18:
    SYNC_BYTE     = 0xAA
    CAL_CMD       = 0x55
    CONF_CMD      = 0x60
    SNAP_CMD      = 0x61
    KEEPALIVE_CMD = 0x62

    CUTOFF_CURRENT = 300
    MAX_CURRENT = 6000

    ACC = 4
    
    PRINT_TX = False
    PRINT_RX = False
    
    # Used to temporarily disable then restore print_tx/rx state
    PRINT_TX_SAVE = False 
    PRINT_RX_SAVE = False
        
    def txrx_print(self, enable = True):
        self.PRINT_TX = enable
        self.PRINT_RX = enable
        
    def txrx_save_and_set(self, enable = True):
        self.PRINT_TX_SAVE = self.PRINT_TX
        self.PRINT_RX_SAVE = self.PRINT_RX
        self.txrx_print(enable)
        
    def txrx_restore(self):
        self.PRINT_TX = self.PRINT_TX_SAVE
        self.PRINT_RX = self.PRINT_RX_SAVE
            

    def __init__(self, port):
        if port is None:
            print("*** NO PORT SPECIFIED ***")
            print("Available serial ports (choose one that says USB somewhere):")
            ports = list_ports.comports()
            
            i = 1
            for p in ports:
                print(f"  {i}: {p.device} - {p.manufacturer} - {p.description}")
                i = i+1
            
            port_id = 0
            while (port_id < 1) or (port_id >= i):
                user_port = input(f"Choose a port (1-{i-1}): ")
                try:
                    port_id = int(user_port)
                except ValueError:
                    print("Invalid input. Please enter a number")
                
            p = ports[port_id - 1]
            print(f"You selected \"{p.device} - {p.manufacturer} - {p.description}\"")
            print(f"In future, use \"m18.py --port {p.device}\" to avoid this menu")
            input("Press Enter to continue")
            
            port = p.device
            
            
        self.port = Serial(port, baudrate=4800, timeout=0.8, stopbits=2)
        self.idle()

    def reset(self):
        """
        Reset the connected device via the serial port.

        This method toggles the `break_condition` and `DTR` signals on the
        serial port to force the device into a reset state. Afterward, it
        sends the synchronization byte (`SYNC_BYTE`) and waits for a
        matching response. This is used for automatic baudrate detection.

        Returns:
            bool: True if the device responded with the expected sync byte,
                False otherwise.
        """
        self.ACC = 4
        self.port.break_condition = True
        self.port.dtr = True
        time.sleep(0.3)
        self.port.break_condition = False
        self.port.dtr = False
        time.sleep(0.3)
        self.send(struct.pack('>B', self.SYNC_BYTE))
        try:
            response = self.read_response(1)
        except ValueError:
            return False
        time.sleep(0.01)
        if response and response[0] == self.SYNC_BYTE:
            return True
        else:
            print(f"Unexpected response: {response}")
            return False

    def update_acc(self):
        acc_values = [0x04, 0x0C, 0x1C]
        current_index = acc_values.index(self.ACC)
        next_index = (current_index + 1) % len(acc_values)
        self.ACC = acc_values[next_index]

    def reverse_bits(self, byte):
        return int(f"{byte:08b}"[::-1], 2)
    
    def checksum(self, payload):
        checksum = 0
        for byte in payload:
            checksum += byte & 0xFFFF
        return checksum

    def add_checksum(self, lsb_command):
        lsb_command += struct.pack(">H", self.checksum(lsb_command)) 
        return lsb_command
    
    def send(self, command):
        self.port.reset_input_buffer()
        debug_print = " ".join(f"{byte:02X}" for byte in command)
        msb = bytearray(self.reverse_bits(byte) for byte in command)
        if self.PRINT_TX:
            print(f"Sending:  {debug_print}")
        self.port.write(msb)
    
    def send_command(self, command):
        self.send(self.add_checksum(command))

    def read_response(self, size):
        msb_response = self.port.read(1)
        if not msb_response or len(msb_response) < 1:
            raise ValueError("Empty response")
        if self.reverse_bits(msb_response[0]) == 0x82:
            msb_response += self.port.read(1)
        else:
            msb_response += self.port.read(size-1)
        lsb_response = bytearray(self.reverse_bits(byte) for byte in msb_response)
        debug_print = " ".join(f"{byte:02X}" for byte in lsb_response)
        if self.PRINT_RX:
            print(f"Received: {debug_print}")
        time.sleep(0.05)
        return lsb_response

    def configure(self, state):
        self.ACC = 4
        self.send_command(struct.pack('>BBBHHHBB', self.CONF_CMD, self.ACC, 8, 
                                    self.CUTOFF_CURRENT, self.MAX_CURRENT, self.MAX_CURRENT, state, 13))
        return self.read_response(5)

    def get_snapchat(self):
        self.send_command(struct.pack('>BBB', self.SNAP_CMD, self.ACC, 0))
        self.update_acc()
        return self.read_response(8)
    
    def keepalive(self):
        self.send_command(struct.pack('>BBB', self.KEEPALIVE_CMD, self.ACC, 0))
        return self.read_response(9)
    
    def calibrate(self):
        self.send_command(struct.pack('>BBB', self.CAL_CMD, self.ACC, 0))
        self.update_acc()
        return self.read_response(8)
    
    def simulate(self):
        print("Simulating charger communication")
        
        self.txrx_save_and_set(True) # Turn on TX/RX messages
        
        self.reset()
        
        self.configure(2)
        self.get_snapchat()
        time.sleep(0.6)
        self.keepalive()
        self.configure(1)
        self.get_snapchat()
        try:
            while True:
                time.sleep(0.5)
                self.keepalive()
        except KeyboardInterrupt:
            print("\nSimulation aborted by user. Exiting gracefully...")
        finally:
            self.idle() 
            
        self.txrx_restore() # restore TX/RX print status
    

    def simulate_for(self, duration):
        # Simulate charging for 'time' seconds
        print(f"Simulating charger communication for {duration} seconds...")
        begin_time = time.time()
        self.reset()
        self.configure(2)
        self.get_snapchat()
        time.sleep(0.6)
        self.keepalive()
        self.configure(1)
        self.get_snapchat()
        try:
            #start_time = time.time()
            while (time.time() - begin_time) < duration:
                time.sleep(0.5)
                self.keepalive()
        except KeyboardInterrupt:
            print("\nSimulation aborted by user. Exiting gracefully...")
        finally:
            self.idle() 
            print(f"Duration: ", time.time() - begin_time)

    def debug(self, a,b,c,length):
        
        # Turn off debug, restore after printing
        rx_debug = self.PRINT_RX
        tx_debug = self.PRINT_TX
        self.PRINT_TX = False
        self.PRINT_RX = False
        
        self.reset()
        self.PRINT_TX = tx_debug
        data = self.cmd(a,b,c,length)
        data_print = " ".join(f"{byte:02X}" for byte in data)
        print(f"Response from: 0x{(a * 0x100 + b):04X}:", data_print)
        self.idle()
        self.PRINT_RX = rx_debug
        
    def try_cmd(self, cmd, msb, lsb, length, ret_len=0):
        # Turn off TX/RX printing, restore after printing
        self.txrx_save_and_set(False)
        
        # default is read 5 bytes more than payload (3-byte header, 2-byte cksum)
        if ret_len == 0:
            ret_len = length + 5
        
        self.reset()
        self.send_command(struct.pack('>BBBBBB', cmd, 0x04, 0x03, msb, lsb, length))
        data = self.read_response(ret_len)
        data_print = " ".join(f"{byte:02X}" for byte in data)
        print(f"Response from: 0x{(msb * 0x100 + lsb):04X}:", data_print)
        self.idle()
        self.txrx_restore()
        
    
    def cmd(self, a,b,c,length, command = 0x01):
        self.send_command(struct.pack('>BBBBBB', command, 0x04, 0x03, a, b, c))
        return self.read_response(length)
        

    def brute(self, a, b, length = 0xFF, command = 0x01):
        self.reset()
        try:
            for i in range(length):
                ret = self.cmd(a, b, i, i+5, command)
                if ret[0] == 0x81:
                    data_print = " ".join(f"{byte:02X}" for byte in ret)
                    print(f"Valid response from: 0x{(a * 0x100 + b):04X} with length: 0x{i:02X}:", data_print)
        except KeyboardInterrupt:
            print("\nSimulation aborted by user. Exiting gracefully...")
        finally:
            self.idle() 

    def full_brute(self, start=0, stop=0xFFFF, length = 0xFF):
        """
        Perform a brute-force query across all register addresses.

        Iterates from `start` to `stop` (exclusive) and calls
        `self.brute(msb, lsb, length, 0x01)` for each address.
        The method splits the 16-bit address into its MSB and LSB
        before passing it along. Progress is printed every 256
        addresses.
        """       
        addr=0
        try:
            for addr in range(start, stop): 
                msb = (addr >> 8) & 0xFF # separate upper 8-bits of addr
                lsb = addr & 0xFF # separate lower 8-bits of addr
                self.brute(msb, lsb, length, 0x01)
                if (addr % 256) == 0:
                    print(f"addr = 0x{addr:04X} ", datetime.datetime.now() )
        except KeyboardInterrupt:
            print("\nSimulation aborted by user. Exiting gracefully...")
            print(f"\nStopped at address: 0x{addr:04X}")
        finally:
            self.idle() 
    
    def wcmd(self, a,b,c,length):
        self.send_command(struct.pack('>BBBBBB', 0x01, 0x05, 0x03, a, b, c))
        return self.read_response(length)

    def write_message(self, message):
        try:
            if len(message) > 0x14:
                print("ERROR: Message too long!")
                return
            print(f"Writing \"{message}\" to memory")
            self.reset()
            message = message.ljust(0x14, '-')
            for i, char in enumerate(message):
                self.wcmd(0,0x23+i,ord(char), 2)
        except Exception as e:
            print(f"write_message: Failed with error: {e}")

    def idle(self):
        self.port.break_condition = True
        self.port.dtr = True

    def high(self):
        self.port.break_condition = False
        self.port.dtr = False
        
    def high_for(self, duration):
        self.high()
        time.sleep(duration)
        self.idle()

    def calculate_temperature(self, adc_value):
        """
        Convert an ADC reading into a temperature estimate.

        The constants used here are only estimated.
        """
        r1 = 10e3  # 10k ohm
        r2 = 20e3  # 20k ohm
        t1 = 50    # 50°C
        t2 = 35    # 35°C

        adc1 = 0x0180
        adc2 = 0x022E
        
        m = (t2 - t1) / (r2 - r1)
        b = t1 - m * r1

        resistance = r1 + (adc_value - adc1) * (r2 - r1) / (adc2 - adc1)
        temperature = m * resistance + b

        return round(temperature, 2)

    def bytes2dt(self, time_bytes):
        epoch_time = int.from_bytes(time_bytes, 'big')
        dt = datetime.datetime.fromtimestamp(epoch_time, tz=datetime.UTC)
        return dt

        
    def read_all(self):
        try:
            self.reset()
            for addr_h, addr_l, length in data_matrix:
                response = self.cmd(addr_h, addr_l, length, (length + 5))
                if response and len(response) >= 4 and response[0] == 0x81:
                    data = response[3:3 + length]
                    data_print = " ".join(f"{byte:02X}" for byte in data)
                    print(f"Response from: 0x{(addr_h * 0x100 + addr_l):04X}:", data_print)
                else:
                    data_print = " ".join(f"{byte:02X}" for byte in response)
                    print(f"Invalid response from: 0x{(addr_h * 0x100 + addr_l):04X} Response: {data_print}")
            self.idle()
        except Exception as e:
            print(f"read_all: Failed with error: {e}")
    

    def read_id(self, id_array=None, force_refresh=True, output="label"):
        """
        Read data by ID. Default is print all
        # id_array - array of registers to print
        # force_refresh - force a read of all registers to ensure they're up to date
        # output - ["label" | "raw" | "array"]
        #       "label" - prints labelled registers to stdout
        #       "raw" - prints values only (for pasting into spreadsheet)
        #       "array" - returns array of [id, value]
        #       "form" - returns array of [value]
        """
        # If empty, default is print all
        if id_array is None or len(id_array) == 0:
            id_array = range(0,len(data_id))
            
        if not ( (output == "label") or (output == "raw") or (output == "array") or (output == "form")):
            print(f"Unrecognised 'output' = {output}. Please choose \"label\", \"raw\", or \"array\"")
            output = "label"
            
        array = []
        
        try:
            self.reset()
            
            if force_refresh:
                # Do dummy read to update 0x9000 data
                for addr_h, addr_l, length in data_matrix:
                    response = self.cmd(addr_h, addr_l, length, (length + 5))
                self.idle()
                time.sleep(0.1)
            
            # Add date to top
            now = datetime.datetime.now()
            formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")
            if output == "label":
                print(formatted_time)
                print("ID  ADDR   LEN TYPE       LABEL                                   VALUE")
            elif output == "raw":
                print(formatted_time)
            elif output == "form":
                array.append(formatted_time)
            
            
            self.reset()
            for i in id_array:
                addr = data_id[i][0]
                addr_h = (addr >> 8) & 0xFF # separate upper 8-bits of addr
                addr_l = addr & 0xFF # separate lower 8-bits of addr
                length = data_id[i][1]
                data_type = data_id[i][2]
                label = data_id[i][3]

                response = self.cmd(addr_h, addr_l, length, (length + 5))
                if response and len(response) >= 4 and response[0] == 0x81:
                    # extract payload. message without header and cksum
                    data = response[3:(3+length)]
                                        
                    # process data according to data_type
                    # (uint, date, ascii, sn, adc_t, dec_t, cell_v)
                    match data_type:
                        case "uint":
                            array_value = value = int.from_bytes(data, 'big')
                        case "date":
                            array_value = self.bytes2dt(data)
                            value = array_value.strftime('%Y-%m-%d %H:%M:%S')
                        case "hhmmss":
                            dur = int.from_bytes(data, 'big')
                            mm, ss = divmod(dur, 60)
                            hh, mm = divmod(mm, 60)
                            array_value = value = f"{hh}:{mm:02d}:{ss:02d}"
                        case "ascii":
                            string = data.decode('utf-8')
                            array_value = value = f'\"{string}\"'
                        case "sn":
                            btype = int.from_bytes(data[0:2],'big')
                            serial = int.from_bytes(data[2:5],'big')
                            if output == "label" or output == "array":
                                array_value = value = f"Type: {btype:3d}, Serial: {serial:d}"
                            else:
                                value = f"{btype}\n{serial}"
                        case "adc_t":
                            array_value = value = self.calculate_temperature(int.from_bytes(data, 'big'))
                        case "dec_t":
                            temp = data[0] + data[1]/256
                            array_value = value = f"{temp:.2f}"
                        case "cell_v":
                            array_value = cv = [int.from_bytes(data[i:i+2], 'big') for i in range(0, 10, 2)]
                            if output == "label":
                                value = f"1: {cv[0]:4d}, 2: {cv[1]:4d}, 3: {cv[2]:4d}, 4: {cv[3]:4d}, 5: {cv[4]:4d}"
                            else:
                                value = f"{cv[0]:4d}\n{cv[1]:4d}\n{cv[2]:4d}\n{cv[3]:4d}\n{cv[4]:4d}"
                   
                else:
                    array_value = None
                    value = "------"
                
                if output == "label":
                    # Print formatted data
                    print(f"{i:3d} 0x{addr:04X} {length:2d} {data_type:>6}   {label:<39} {value:<}")
                elif output == "raw":
                    # Print spreadsheet format
                    print(value)
                elif output == "array":
                    array.append([i, array_value])
                elif output == "form":
                    # Print spreadsheet format
                    array.append(value)
                    
            if (output == "array" or output == "form") and array:
                return array
                    
            self.idle()
        except Exception as e:
            print(f"read_id: Failed with error: {e}")

    
    def read_all_spreadsheet(self):
        try:
            self.reset()
            
            # Do dummy read to update 0x9000 data
            for addr_h, addr_l, length in data_matrix:
                self.cmd(addr_h, addr_l, length, (length + 5))
            self.idle()
            time.sleep(0.5)
            
            self.reset()
            
            # Add date to top
            now = datetime.datetime.now()
            formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")
            print(formatted_time)
            
            for addr_h, addr_l, length in data_matrix:
                response = self.cmd(addr_h, addr_l, length, (length + 5))
                if response and len(response) >= 4 and response[0] == 0x81:
                    # extract payload. message without header and cksum
                    data = response[3:(3+length)]
                    print(f"0x{(addr_h * 0x100 + addr_l):04X}")
                    if len(data) == 0:
                        print("EMPTY")
                    else:
                        # data_print = "\n".join(f"{byte:02X}" for byte in data)
                        data_print = "\n".join(f"{byte}" for byte in data)
                        print(data_print)
                else:
                    print(f"0x{(addr_h * 0x100 + addr_l):04X}")
                    data_print = " ".join(f"{byte:02X}" for byte in response)
                    print(f"INV: {data_print}")
                    # pad with "blank" so data lines up in spreadsheet
                    for i in range(1,length): 
                        print("blank")
                    
            self.idle()
        except Exception as e:
            print(f"read_all_spreadsheet: Failed with error: {e}")
            
    
    def health_data(self, force_refresh = True):
        """
        Read the registers health() reports on and return them as a dict.

        Same wire traffic as health(). The difference is that the derived
        values - imbalance, discharge cycles, total time on tool, the
        histogram percentages - are returned rather than being formatted
        into a print() and thrown away.

        Each measurement is a dict of {"value": ..., "valid": bool}, where
        "valid" is False if the register did not answer or answered with a
        sentinel. Nothing here is rounded or formatted for display; that is
        health()'s job.

        Date fields hold datetime objects rather than strings, so a caller
        writing this out as JSON needs an encoder for them
        (json.dumps(..., default=str) is enough).

        Returns None if the read failed outright.
        """
        reg_list = list(HEALTH_REGISTERS.values()) + list(HEALTH_BUCKET_REGISTERS)
        raw = self.read_id(reg_list, force_refresh, "array")
        if not raw:
            return None
        by_reg = dict(raw)

        def value_of(name):
            return by_reg.get(HEALTH_REGISTERS[name])

        # Identity. The e-serial does NOT match the serial printed on the
        # case; there is no register that returns the case serial.
        serial_raw = value_of("serial_raw")
        numbers = findall(r'\d+\.?\d*', serial_raw) if isinstance(serial_raw, str) else []
        bat_type = numbers[0] if len(numbers) > 0 else None
        e_serial = numbers[1] if len(numbers) > 1 else None
        type_known = bat_type in bat_lookup
        capacity_ah, description = bat_lookup.get(bat_type, [0, "Unknown"])

        data = {
            "battery_type": health_field(bat_type),
            "battery_description": health_field(description, type_known),
            "capacity_ah": health_field(capacity_ah, type_known),
            "e_serial": health_field(e_serial),
        }

        # Dates. Day counts are measured against the battery's own clock,
        # not the host's.
        manufacture_date = value_of("manufacture_date")
        battery_date = value_of("battery_date")
        last_tool_use = value_of("last_tool_use_date")
        last_charge = value_of("last_charge_date")
        data["manufacture_date"] = health_field(manufacture_date, date_valid(manufacture_date))
        data["battery_date"] = health_field(battery_date, date_valid(battery_date))
        data["days_since_first_charge"] = health_field(value_of("days_since_first_charge"))
        data["days_since_last_tool_use"] = health_field(
            days_between(battery_date, last_tool_use),
            date_valid(battery_date) and date_valid(last_tool_use))
        data["days_since_last_charge"] = health_field(
            days_between(battery_date, last_charge),
            date_valid(battery_date) and date_valid(last_charge))

        # Cells.
        cells = value_of("cell_voltages_mv")
        if isinstance(cells, list) and cells:
            pack_voltage = sum(cells) / 1000
            imbalance = max(cells) - min(cells)
        else:
            pack_voltage = imbalance = None
        cells_valid = (isinstance(cells, list) and bool(cells)
                       and all(v not in UINT_SENTINELS for v in cells))
        data["cell_voltages_mv"] = health_field(cells, cells_valid)
        data["pack_voltage_v"] = health_field(pack_voltage, cells_valid)
        data["cell_imbalance_mv"] = health_field(imbalance, cells_valid)

        # Only one of these answers on any given pack, depending on whether
        # it is a Forge. Both are passed through untouched.
        data["temperature_c"] = health_field(value_of("temperature_c"))
        data["temperature_c_forge"] = health_field(value_of("temperature_c_forge"))

        for name in ("charge_count_redlink", "charge_count_dumb", "charge_count_total",
                     "total_charge_time", "time_idle_on_charger", "low_voltage_charges",
                     "discharged_to_empty", "times_overheated", "overcurrent_events",
                     "low_voltage_events", "low_voltage_bounce"):
            data[name] = health_field(value_of(name))

        # Discharge totals. Cycles need a known capacity to divide by.
        discharge_as = value_of("total_discharge_as")
        discharge_valid = discharge_as is not None and discharge_as not in UINT_SENTINELS
        if isinstance(discharge_as, int):
            total_discharge_ah = discharge_as / 3600
            cycles = discharge_as / 3600 / capacity_ah if capacity_ah else None
        else:
            total_discharge_ah = cycles = None
        data["total_discharge_as"] = health_field(discharge_as)
        data["total_discharge_ah"] = health_field(total_discharge_ah, discharge_valid)
        data["total_discharge_cycles"] = health_field(
            cycles, type_known and cycles is not None and discharge_valid)

        # Discharge histogram. tool_time is the sum of exactly these
        # buckets, so when it is zero every bucket is zero too and the
        # percentage is 0 rather than a division by zero (issue #47).
        bucket_values = [by_reg.get(reg) for reg in HEALTH_BUCKET_REGISTERS]
        tool_time = sum(v for v in bucket_values if isinstance(v, int))
        buckets = []
        for amp_range, value in zip(HEALTH_BUCKET_RANGES, bucket_values):
            seconds = value if isinstance(value, int) else 0
            buckets.append({
                "range": amp_range,
                "seconds": seconds,
                "percent": round((seconds / tool_time) * 100) if tool_time else 0,
                "valid": value is not None and value not in UINT_SENTINELS,
            })
        data["discharge_buckets"] = buckets
        data["tool_time_s"] = health_field(
            tool_time, all(bucket["valid"] for bucket in buckets))

        return data

    def health(self, force_refresh = True):
        """
        Print labelled and formatted summary of key data.
        Some data is calculated, like 'imbalance' and 'total time on tool'
        Print simple histogram of discharge stats
        """
        # turn off debugging messages
        self.txrx_save_and_set(False)
        
        try:
            print("Reading battery. This will take 5-10sec\n")
            data = self.health_data(force_refresh)
            
            print(f"Type: {data['battery_type']['value']} [{data['battery_description']['value']}]")
            print("E-serial:", data['e_serial']['value'], "(does NOT match case serial)")
            
            print("Manufacture date:", data['manufacture_date']['value'].strftime('%Y-%m-%d') )
            print("Days since 1st charge:", data['days_since_first_charge']['value'])
            print("Days since last tool use:", data['days_since_last_tool_use']['value'] )
            print("Days since last charge:", data['days_since_last_charge']['value'] )
            print("Pack voltage:", data['pack_voltage_v']['value'] )
            print("Cell Voltages (mV):", data['cell_voltages_mv']['value'] )
            print("Cell Imbalance (mV):", data['cell_imbalance_mv']['value'] )
            if data['temperature_c']['value']:
                print("Temperature (deg C):", data['temperature_c']['value'])
            if data['temperature_c_forge']['value']:
                print("Temperature (deg C):", data['temperature_c_forge']['value'])
            
            print("\nCHARGING STATS:")
            print(f"Charge count [Redlink, dumb, (total)]: {data['charge_count_redlink']['value']}, {data['charge_count_dumb']['value']}, ({data['charge_count_total']['value']})")
            print("Total charge time:", data['total_charge_time']['value'])
            print("Time idling on charger:", data['time_idle_on_charger']['value'])
            print("Low-voltage charges (any cell <2.5V):", data['low_voltage_charges']['value'])
            
            print("\nTOOL USE STATS:")
            print("Total discharge (Ah):", f"{data['total_discharge_ah']['value']:.2f}")
            if data['capacity_ah']['valid']:
                total_discharge_cycles = f"{data['total_discharge_cycles']['value']:.2f}"
            else:
                total_discharge_cycles = 'Unknown battery type, unable to calculate'
            print("Total discharge cycles:", total_discharge_cycles)
            print("Times discharged to empty:", data['discharged_to_empty']['value'])
            print("Times overheated:", data['times_overheated']['value'])
            print("Overcurrent events:", data['overcurrent_events']['value'])
            print("Low-voltage events:", data['low_voltage_events']['value'])
            print("Low-voltage bounce/stutter:", data['low_voltage_bounce']['value'])
            
            print("Total time on tool (>10A):", datetime.timedelta(seconds=data['tool_time_s']['value']))
                
            for bucket in data['discharge_buckets']:
                label = f"Time @ {bucket['range']:>8}:"
                hhmmss = datetime.timedelta(seconds=bucket['seconds'])
                pct = bucket['percent']
                bar = "X" * round(pct)
                print(label, hhmmss, f"{pct:2d}%", bar)
                
        except Exception as e:
            print(f"health: Failed with error: {e}")
            print("Check battery is connected and you have correct serial port")
            
        # restore debug status
        self.txrx_restore()



    def submit_form(self):
        form_url = 'https://docs.google.com/forms/d/e/1FAIpQLScvTbSDYBzSQ8S4XoF-rfgwNj97C-Pn4Px3GIixJxf0C1YJJA/formResponse'

        # Get data from battery
        print("Getting data from battery...")
        output = self.read_id(output="form")

        if output is None:
            print("submit_form: No output returned, aborting")
        s_output = "\n".join(map(str, output))


        # Prompt the user for each field
        print("Please provide this information. All the values can be found on the label under the battery.")
        one_key_id = input("Enter One-Key ID (example: H18FDCAD): ")
        date = input("Enter Date (example: 190316): ")
        serial_number = input("Enter Serial number (example: 0807426): ")
        sticker = input("Enter Sticker (example: 4932 4512 45): ")
        model_type = input("Enter Type (example: M18B9): ")
        capacity = input("Enter Capacity (example: 9.0Ah): ")

        
        form_data = {
            # One-Key ID (H18FDCAD)
            #   Option: any text
            "entry.905246449": one_key_id,
            # Date (190316)
            #   Option: any text
            "entry.453401884": date,
            # Serial number (0807426) (required)
            #   Option: any text
            "entry.2131879277": serial_number,
            # Sticker (4932 4512 45)
            #   Option: any text
            "entry.337435885": sticker,
            # Type (M18B9) (required)
            #   Option: any text
            "entry.1496274605": model_type,
            # Capacity (9.0Ah) (required)
            #   Option: any text
            "entry.324224550": capacity,
            # Output from m18-protocol (required)
            #   Option: any text
            "entry.716337020": s_output
        }

        # Submit the form
        response = post(form_url, data=form_data)

        # Check response
        if response.status_code == 200:
            print("Form submitted successfully!")
        else:
            print(f"submit_form: Failed to submit form. Status code: {response.status_code}")



    def help(self):
        print("Functions: \n \
            DIAGNOSTICS: \n \
            m.health() - print simple health report on battery \n \
            m.read_id() - print labelled and formatted diagnostics \n \
            m.read_id(output=\"raw\") - print in spreadsheet format \n \
            m.submit_form() - prompts for manual inputs and submits battery diagnostics data \n \
            \n \
            m.help() - this message\n \
            m.adv_help() - advanced help\n \
            \n \
            exit() - end program\n")
           
    def adv_help(self):
        print("Advanced functions: \n \
            m.read_all() - print all known bytes in 0x01 command \n \
            m.read_all_spreadsheet() - print bytes in spreadsheet format \n \
            \n \
            CHARGING SIMULATION: \n \
            m.simulate() - simulate charging comms \n \
            m.simulate_for(t) - simulate for t seconds \n \
            m.high_for(t) - bring J2 high for t sec, then idle \n \
            \n \
            m.write_message(message) - write ascii string to 0x0023 register (20 chars)\n \
            \n \
            Debug: \n \
            m.PRINT_TX = True - boolean to enable TX messages \n \
            m.PRINT_RX = True - boolean to enable RX messages \n \
            m.txrx_print(bool) - set PRINT_TX & RX to bool \n \
            m.txrx_save_and_set(bool) - save PRINT_TX & RX state, then set both to bool \n \
            m.txrx_restore() - restore PRINT_TX & RX to saved values \n \
            m.brute(addr_msb, addr_lsb) \n \
            m.full_brute(start, stop, len) - check registers from 'start' to 'stop'. look for 'len' bytes \n \
            m.debug(addr_msb, addr_lsb, len, rsp_len) - send reset() then cmd() to battery \n \
            m.try_cmd(cmd, addr_h, addr_l, len) - try 'cmd' at [addr_h addr_l] with 'len' bytes \n \
            \n \
            Internal:\n \
            m.high() - bring J2 pin high (20V)\n \
            m.idle() - pull J2 pin low (0V) \n \
            m.reset() - send 0xAA to battery. Return true if battery replies wih 0xAA \n \
            m.get_snapchat() - request 'snapchat' from battery (0x61)\n \
            m.configure() - send 'configure' message (0x60, charger parameters)\n \
            m.calibrate() - calibration/interrupt command (0x55) \n \
            m.keepalive() - send charge current request (0x62) \n") 


if __name__ == '__main__':
    parser = ArgumentParser(
        description="M18 Protocol Interface",
        epilog="Connect UART-TX to M18-J2 and UART-RX to M18-J1 to fake the charger and UART-GND to M18-GND")
    parser.add_argument('--port', type=str, help="Serial port to connect to (e.g., COM5)")
    parser.add_argument('--health', action='store_true', help='Print health report and exit')
    parser.add_argument('--ss', action='store_true', help='Spreadsheet output: Print all register values and exit')
    parser.add_argument('--idle', action='store_true', help='Set TX=Low and exit. Prevents unwanted charge increments')
    args = parser.parse_args()

    # --ss flag must also have --port set.
    # This prevents 'm18.py --ss | clip.exe' getting stuck in menu they can't see
    if (args.port is None) and args.ss:
        print("You must specify a port. E.g. \"--port COM5\"")
    else:
        m = M18(args.port)
        if args.idle:
            m.idle()
            print("TX should now be low voltage (<1V). Safe to connect")
        elif args.health:
            m.health()
        elif args.ss:
            m.read_id(output="raw")
        else:
            m.help()
            code.InteractiveConsole(locals = locals()).interact('Entering shell...')    
    
