# Platypus

> Flash various BMC components

Platypus utilizes serial connection and Redfish API to flash various BMC 
components. The simple UI removes the need to type commands into command line
making Platypus ideal for flashing components or different BMCs consecutively.

---

## Installation

Platypus operates on Ubuntu 24.04 though you may be able to compile Platypus on a different version of Ubuntu.

#####  Method One:
Download the lateset release onto your system.
Enter CLI and type the following commands:
  ```sh
  sudo chmod +x ./dep.run 
sudo ./dep.run
  ```
Once all dependencies are installed properly, run Platypus:
  ```sh
  sudo ./Platypus
  ```

##### Method Two: 
Clone the repository and run the dep.run file.
Enter CLI and type the following commands:
  ```sh
  sudo chmod +x ./dep.run 
	sudo ./dep.run
  ```
- Once all dependencies are installed properly, you can package Platypus yourself:
  ```sh
  nicegui-pack --onefile --name "myapp" main.py
  ```
- This will create a dist and build directory. There will be an executable that you can run in the dist folder.

##### Usage:

## Set BMC IP:
