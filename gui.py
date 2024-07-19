from nicegui import app, ui
import bmc as bmc




fw_content = None
flash_file = None



def output_message(message):
    print(message)
    status.push(message)



def update_progress(value):
    if value is not None and 0 <= value <= 1:
            progress_bar.set_value(value)
            output_message(f"progress: {value*100}%")
    else:
        output_message(f"Invalid progress value: {value}")



async def update_button():
    fw_path = await choose_file()
    if fw_path:
        with open(fw_path, 'rb') as fw_file:
            fw_content = fw_file.read()
        await bmc.bmc_update(username.value, password.value, bmc_ip.value, fw_content, update_progress, output_message)
    else:
        ui.notify("Please upload a firmware file first.", position='top')

def update_ui_info(info):
    if info:
        health_label.set_text(f"Health: {info.get('Status', {}).get('Health', 'Unknown')}")
        power_label.set_text(f"Power: {info.get('PowerState', 'Unknown')}")
        firmware_version_label.set_text(f"Firmware Version: {info.get('FirmwareVersion', 'Unknown')}")
        name_model_text = f"Device: {info.get('Manufacturer', 'Unknown')} {info.get('Model', 'Unknown')}"
        manufacturer_model.set_text(name_model_text)



async def ip_button():
    await bmc.set_ip(bmc_ip.value, username.value, password.value, update_progress, output_message)



async def choose_file():
    global flash_file
    files = await app.native.main_window.create_file_dialog(allow_multiple=False)
    if files: 
        flash_file = files[0]
        ui.notify(f"Selected fle: {flash_file}", position='top')
        return flash_file
    else:
        ui.notify("No file selected.", position='top')



async def flashub_button():
    flash_file = await choose_file()
    if flash_file:
        await bmc.flasher(username.value, password.value, flash_file, your_ip.value, update_progress, output_message)



def reset_button():
    bmc.reset_ip(username.value, password.value, bmc_ip.value, update_progress, output_message)



def on_upload(event):
    global fw_content
    fw_content = event.content.read()
    ui.notify(f'Uploaded {event.name}', position='top')



def load_info():
    info = bmc.bmc_info()
    #update_ui_info(info)
    



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
                password = ui.input(placeholder='Password').classes('w-72').props('rounded outlined dense type=password')
                bmc_ip = ui.input(placeholder='BMC IP').classes('w-72').props('rounded outlined dense')
                your_ip = ui.input(placeholder='U-Boot Server IP').classes('w-72').props('rounded outlined dense')

        # Row for grid of buttons
        with ui.grid(columns=2).style('margin: 0 auto;'):
            ui.button('Update BMC', on_click=update_button).classes('w-48 h-10 rounded-lg')
            ui.button('Set BMC IP', on_click=ip_button).classes('w-48 h-10 rounded-lg')
            ui.button('Reset BMC', on_click=reset_button).classes('w-48 h-10 rounded-lg')
            ui.button('Flash U-Boot', on_click=flashub_button).classes('w-48 h-10 rounded-lg')

        # Log box
        status = ui.log().classes('h-75 w-86').style('margin: 0 auto; margin-top: 15px;')

    # 2nd column
    with ui.card(align_items='start').classes('no-shadow border-[0px] w-96 h-75').style('background-color:#121212; margin-left: 15px; margin-top: 15px;'):
        ui.label('BMC Information:').classes('text-left').style('font-size: 20px;')
        ui.button("Load info", on_click=load_info)
        with ui.column():
            manufacturer_model = ui.label('Device: ').classes('w-72')
            power_label = ui.label('Power State: ').classes('w-72')
            health_label = ui.label('Health: ').classes('w-72')
            firmware_version_label = ui.label('Firmware Version: ').classes('w-72')

        

progress_bar = ui.linear_progress(value=0, show_value=False).classes('w-4/5 h-2 rounded-lg absolute-bottom').style('margin: 0 auto; margin-bottom: 5px')
progress_bar.visible = True

app.native.window_args['resizable'] = False

ui.run(native=True, dark=True, title='Platypus', window_size=(850, 750), reload=False, port=8000)