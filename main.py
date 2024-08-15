from nicegui import app, ui
import bmc as bmc
from contextlib import contextmanager
import subprocess
import os




fw_content = None
flash_file = None
timer = None


# Displays messages in terminal and in the ui 
def output_message(message):
    print(message)
    status.push(message)



# Updates the value of the progress bar 
def update_progress(value):
    if value is not None and 0 <= value <= 1:
            progress_bar.set_value(value)
            #output_message(f"progress: {value*100}%")
    else:
        output_message(f"Invalid progress value: {value}")


class StatusLabel(ui.label):
    def _handle_text_change(self, text: str) -> None:
        super()._handle_text_change(text)
        if 'ttyUSB0' in text:
            self.classes(replace='text-positive')
        else:
            self.classes(replace='text-negative')



buttons = []

@contextmanager
def disable():
    for button in buttons:
        button.disable()
    try:
        yield
    finally:
        for button in buttons:
            button.enable()


def usb_connected():
    return os.path.exists('/dev/ttyUSB0')


def update_usb_status():
    if usb_connected():
        usb_status_label.text = 'BMC detected on ttyUSB0'
        for button in buttons:
            button.enable()
    else:
        usb_status_label.text = 'BMC not detected'
        for button in buttons:
            button.disable()

        health_label.set_text("Health: ")
        power_label.set_text("Power State: ")
        firmware_version_label.set_text("Firmware Version: ")
        manufacturer_model.set_text("Device: ")
        ip_label.set_text(f"IP Address: ")




# Checks for firmware file before flashing the firmware file 
async def update_button():
    timer.deactivate()
    if not username.value or not password.value or not bmc_ip.value:
        missing_fields = []
        if not username.value:
            missing_fields.append("Username")
        if not password.value:
            missing_fields.append("Password")
        if not bmc_ip.value:
            missing_fields.append("IP Address")
        
        # Prompt the user to fill in the missing fields
        ui.notify(f"Please enter the following: {', '.join(missing_fields)}.", position='top')
        return

    with disable():
        output_message('Please pick a firmware file to flash.')
        fw_path = await choose_file()
        if fw_path:
            with open(fw_path, 'rb') as fw_file:
                fw_content = fw_file.read()
            await bmc.bmc_update(username.value, password.value, bmc_ip.value, fw_content, update_progress, output_message)
        else:
            ui.notify("Please upload a firmware file first.", position='top')
    timer.activate


# Initiates setting temporary ip address
async def ip_button():
    timer.deactivate()
    if not username.value or not password.value or not bmc_ip.value:
        missing_fields = []
        if not username.value:
            missing_fields.append("Username")
        if not password.value:
            missing_fields.append("Password")
        if not bmc_ip.value:
            missing_fields.append("IP Address")
        
        # Prompt the user to fill in the missing fields
        ui.notify(f"Please enter the following: {', '.join(missing_fields)}.", position='top')
        return
    
    with disable():
        await bmc.set_ip(bmc_ip.value, username.value, password.value, update_progress, output_message)
    timer.activate()


# Opens a file dialog -> returns path only
async def choose_file():
    global flash_file
    files = await app.native.main_window.create_file_dialog(allow_multiple=False)
    if files: 
        flash_file = files[0]
        ui.notify(f"Selected fle: {flash_file}", position='top')
        return flash_file
    else:
        ui.notify("No file selected.", position='top')



def choose_directory():
    result = subprocess.run(['zenity', '--file-selection', '--directory'], capture_output=True, text=True)
    directory = result.stdout.strip()
    if directory:
        ui.notify(f"Selected directory: {directory}")
        return directory
    else:
        ui.notify("No directory selected.")
        return None
    

# Pick a file before initiating flashing the U-Boot 
async def flashub_button():
    timer.deactivate()
    if not username.value or not password.value or not your_ip.value:
        missing_fields = []
        if not username.value:
            missing_fields.append("Username")
        if not password.value:
            missing_fields.append("Password")
        if not your_ip.value:
            missing_fields.append("Host IP")
        
        # Prompt the user to fill in the missing fields
        ui.notify(f"Please enter the following: {', '.join(missing_fields)}.", position='top')
        return
    with disable():
        flash_file = await choose_file()
        if flash_file:
            await bmc.flasher(username.value, password.value, flash_file, your_ip.value, update_progress, output_message)
    timer.activate()

# Calls the network wipe 
async def net_reset_button():
    timer.deactivate
    if not username.value or not password.value or not bmc_ip.value:
        missing_fields = []
        if not username.value:
            missing_fields.append("Username")
        if not password.value:
            missing_fields.append("Password")
        if not bmc_ip.value:
            missing_fields.append("IP Address")
        
        # Prompt the user to fill in the missing fields
        ui.notify(f"Please enter the following: {', '.join(missing_fields)}.", position='top')
        return
    with disable():
        await bmc.reset_ip(username.value, password.value, bmc_ip.value, update_progress, output_message)
    timer.activate()


# Not sure if I use this currently...
def on_upload(event):
    global fw_content
    fw_content = event.content.read()
    ui.notify(f'Uploaded {event.name}', position='top')



# orgainzes various information regarding the bmc
def update_ui_info(info):
    with disable():
        if info:
            health_label.set_text(f"Health: {info.get('Status', {}).get('Health', 'Unknown')}")
            power_label.set_text(f"Power: {info.get('PowerState', 'Unknown')}")
            firmware_version_label.set_text(f"Firmware Version: {info.get('FirmwareVersion', 'Unknown')}")
            name_model_text = f"Device: {info.get('Manufacturer', 'Unknown')} {info.get('Model', 'Unknown')}"
            manufacturer_model.set_text(name_model_text)



# Updates the ui to display the current ip address
def update_ip(current_ip):
    ip_label.set_text(f"IP Address: {current_ip}")



# Grabs the current ip address of the bmc 
async def load_ip():
    current_ip = await bmc.grab_ip(username.value, password.value, output_message)
    update_ip(current_ip)


# Grabs various information regarding the bmc
async def load_info():
    timer.deactivate()
    if not username.value or not password.value:
        missing_fields = []
        if not username.value:
            missing_fields.append("Username")
        if not password.value:
            missing_fields.append("Password")
        
        # Prompt the user to fill in the missing fields
        ui.notify(f"Please enter the following: {', '.join(missing_fields)}.", position='top')
        return

    if not bmc_ip.value:
        ui.notify("Enter BMC IP Address for more information.")
    
    
    with disable():
        if bmc_ip.value:
            info = bmc.bmc_info(username.value, password.value, bmc_ip.value, output_message)
            update_ui_info(info)
        await load_ip()
    timer.activate()



# Pick a file before initiating factory reset 
async def emmc_button():
    timer.deactivate()
    if not username.value or not password.value or not bmc_ip.value or not radio.value or not your_ip.value:
        missing_fields = []
        if not username.value:
            missing_fields.append("Username")
        if not password.value:
            missing_fields.append("Password")
        if not bmc_ip.value:
            missing_fields.append("IP Address")
        if not radio.value:
            missing_fields.append("BMC Type")
        if not your_ip.value:
            missing_fields.append("Host IP")
        
        # Prompt the user to fill in the missing fields
        ui.notify(f"Please enter the following: {', '.join(missing_fields)}.", position='top')
        return
    with disable():
        dd_value = radio.value
        directory = choose_directory()
        if directory:
            await bmc.flash_emmc(bmc_ip.value, directory, your_ip.value, dd_value, update_progress, output_message)
        else: 
            ui.notify("Please choose a directory")

    timer.activate()



async def power_host():
    timer.deactivate()
    if not username.value or not password.value:
        missing_fields = []
        if not username.value:
            missing_fields.append("Username")
        if not password.value:
            missing_fields.append("Password")
        ui.notify(f"Please enter the following: {', '.join(missing_fields)}.", position='top')
        return

    with disable():
        await bmc.power_host(username.value, password.value, output_message)
    timer.activate()



async def reboot_bmc():
    timer.deactivate()
    if not username.value or not password.value:
        missing_fields = []
        if not username.value:
            missing_fields.append("Username")
        if not password.value:
            missing_fields.append("Password")
        ui.notify(f"Please enter the following: {', '.join(missing_fields)}.", position='top')
        return

    with disable():
        await bmc.reboot_bmc(username.value, password.value, output_message)
    timer.activate()



async def reset_bmc():
    timer.deactivate()
    if not username.value or not password.value:
        missing_fields = []
        if not username.value:
            missing_fields.append("Username")
        if not password.value:
            missing_fields.append("Password")
        ui.notify(f"Please enter the following: {', '.join(missing_fields)}.", position='top')
        return

    with disable():
        await bmc.reset_uboot(output_message)
    timer.activate()




ui.label('https://github.com/rivan2k').classes('absolute top-0 left-0 text-xs text-gray-800 p-2')

# Row to contain both existing and new elements side by side
with ui.row().classes('w-full items-start'):
    # Column for input elements, grid of buttons, and log box
    with ui.column().classes('w-96'):
        with ui.card().classes('no-shadow border-[0px] w-96 h-75').style('background-color:#121212; margin: 0 auto; margin-top: 15px;'):
            with ui.row().classes('w-full'):
                ui.label('Interactions:').classes('text-left').style('font-size: 20px; text-align: left; padding-right: 90px;')
            with ui.row().classes('justify-center'):
                username = ui.input(placeholder='Username').classes('w-72').props('rounded outlined dense')
                password = ui.input(placeholder='Password').classes('w-72').props('rounded outlined dense')  # type=password
                bmc_ip = ui.input(placeholder='BMC IP').classes('w-72').props('rounded outlined dense')
                your_ip = ui.input(placeholder='HOST IP').classes('w-72').props('rounded outlined dense')

        # Row for grid of buttons
        with ui.grid(columns=2).style('margin: 0 auto;'):
            buttons.append(ui.button('Update BMC', on_click=update_button).classes('w-48 h-10 rounded-lg'))
            buttons.append(ui.button('Set BMC IP', on_click=ip_button).classes('w-48 h-10 rounded-lg'))
            buttons.append(ui.button('Network Reset', on_click=net_reset_button).classes('w-48 h-10 rounded-lg'))
            buttons.append(ui.button('Flash U-Boot', on_click=flashub_button).classes('w-48 h-10 rounded-lg'))
            buttons.append(ui.button('Power ON Host', on_click=power_host).classes('w-48 h-10 rounded-lg'))
            buttons.append(ui.button('Reboot BMC', on_click=reboot_bmc).classes('w-48 h-10 rounded-lg'))

    # 2nd column
    with ui.card(align_items='start').classes('no-shadow border-[0px] w-96 h-75').style('background-color:#121212; margin-left: 15px; margin-top: 15px;'):
        ui.label('BMC Information:').classes('text-left').style('font-size: 20px;')
        buttons.append(ui.button("Load info", on_click=load_info))
        usb_status_label = StatusLabel('Checking USB connection...')
        with ui.grid(columns=2).style('margin: 0 auto;'):
            manufacturer_model = ui.label('Device: ').classes('w-72')
            power_label = ui.label('Power: ').classes('w-72')
            health_label = ui.label('Health: ').classes('w-72')
            ip_label = ui.label('IP Address: ').classes('w-72')
        firmware_version_label = ui.label('Firmware Version: ').classes('w-72')
        with ui.row():
            ui.label('Bootloader:').classes('text-left').style('font-size: 20px; text-align: left; padding-right: 90px;')
            buttons.append(ui.button('Flash eMMC', on_click=emmc_button).classes('w-48 h-10 rounded-lg'))
            with ui.dropdown_button(icon='settings', auto_close=True) as dropdown:
                buttons.append(dropdown)
                with ui.row():
                    radio = ui.radio({1:'MOS BMC', 2:'Nano BMC'})
        buttons.append(ui.button('Reset BMC', on_click=reset_bmc).style('width: 300px;').classes('rounded-lg'))


# Log box
status = ui.log().classes('h-75 w-86').style('margin: 0 auto; margin-top: 15px;')
            

progress_bar = ui.linear_progress(value=0, show_value=False).classes('w-4/5 h-2 rounded-lg absolute-bottom').style('margin: 0 auto; margin-bottom: 10px')
progress_bar.visible = True

app.native.window_args['resizable'] = True



update_usb_status()
timer = ui.timer(1.0, update_usb_status)
ui.run(native=True, dark=True, title='Platypus', window_size=(950, 850), reload=False, port=8081, host='0.0.0.0')

