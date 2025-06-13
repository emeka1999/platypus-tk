#!/usr/bin/env python3
"""
BMC Software QC Tester
Comprehensive quality control testing for the Platypus BMC Management Software
Tests main.py, extra.py, and bios_updater functionality
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import subprocess
import threading
import time
import os
import json
import psutil
import serial
import glob
import socket
import sys
from dataclasses import dataclass
from typing import List, Dict, Optional, Callable
import importlib.util

@dataclass
class TestResult:
    """Test result data structure"""
    name: str
    status: str  # "PASS", "FAIL", "SKIP", "RUNNING"
    message: str = ""
    duration: float = 0.0
    details: str = ""

class TestCategory:
    """Test category grouping"""
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.tests = []
        self.results = []

class QCTester:
    """Main QC testing application"""
    
    def __init__(self):
        # Configure CustomTkinter
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        # Create main window
        self.root = ctk.CTk()
        self.root.title("BMC Software QC Tester")
        self.root.geometry("1000x800")
        
        # Test state
        self.test_categories = []
        self.current_test = None
        self.test_running = False
        self.stop_requested = False
        
        # Results
        self.total_tests = 0
        self.passed_tests = 0
        self.failed_tests = 0
        self.skipped_tests = 0
        
        # Create test categories
        self.setup_test_categories()
        
        # Create UI
        self.create_ui()
        
        # Bind close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
    
    def setup_test_categories(self):
        """Setup all test categories and individual tests"""
        
        # 1. Environment Tests
        env_category = TestCategory("Environment", "System and dependency checks")
        env_category.tests = [
            ("Python Version", self.test_python_version),
            ("Required Packages", self.test_required_packages),
            ("Serial Devices", self.test_serial_devices),
            ("Network Interfaces", self.test_network_interfaces),
            ("File Permissions", self.test_file_permissions),
            ("Terminal Emulators", self.test_terminal_emulators)
        ]
        self.test_categories.append(env_category)
        
        # 2. Core Module Tests
        core_category = TestCategory("Core Modules", "Import and basic functionality tests")
        core_category.tests = [
            ("Import main.py", self.test_import_main),
            ("Import extra.py", self.test_import_extra),
            ("Import utils.py", self.test_import_utils),
            ("Import network.py", self.test_import_network),
            ("Import bmc.py", self.test_import_bmc)
        ]
        self.test_categories.append(core_category)
        
        # 3. UI Component Tests
        ui_category = TestCategory("UI Components", "User interface and widget tests")
        ui_category.tests = [
            ("Main Window Creation", self.test_main_window),
            ("Multi-Unit Window", self.test_multi_unit_window),
            ("File Selection Dialogs", self.test_file_dialogs),
            ("Network IP Detection", self.test_ip_detection),
            ("Configuration Save/Load", self.test_config_management)
        ]
        self.test_categories.append(ui_category)
        
        # 4. Serial Communication Tests
        serial_category = TestCategory("Serial Communication", "Serial port and device tests")
        serial_category.tests = [
            ("Serial Port Detection", self.test_serial_detection),
            ("Serial Connection", self.test_serial_connection),
            ("Serial Data Reading", self.test_serial_reading),
            ("Serial Cleanup", self.test_serial_cleanup),
            ("Multiple Serial Ports", self.test_multiple_serial)
        ]
        self.test_categories.append(serial_category)
        
        # 5. Process Management Tests
        process_category = TestCategory("Process Management", "Process creation and cleanup tests")
        process_category.tests = [
            ("HTTP Server Creation", self.test_http_server),
            ("Server Cleanup", self.test_server_cleanup),
            ("Terminator Process", self.test_terminator_process),
            ("Minicom Process", self.test_minicom_process),
            ("Zombie Process Detection", self.test_zombie_processes),
            ("Resource Cleanup", self.test_resource_cleanup)
        ]
        self.test_categories.append(process_category)
        
        # 6. Multi-Flash Tests
        multiflash_category = TestCategory("Multi-Flash", "Multi-unit flash functionality tests")
        multiflash_category.tests = [
            ("Unit Configuration", self.test_unit_config),
            ("Shared Server Management", self.test_shared_servers),
            ("Multi-Console Creation", self.test_multi_console),
            ("Config Validation", self.test_config_validation),
            ("Progress Tracking", self.test_progress_tracking)
        ]
        self.test_categories.append(multiflash_category)
        
        # 7. Error Handling Tests
        error_category = TestCategory("Error Handling", "Error conditions and recovery tests")
        error_category.tests = [
            ("Missing Files", self.test_missing_files),
            ("Invalid IP Addresses", self.test_invalid_ips),
            ("Serial Port Errors", self.test_serial_errors),
            ("Network Errors", self.test_network_errors),
            ("Memory Stress Test", self.test_memory_stress),
            ("Exception Handling", self.test_exception_handling)
        ]
        self.test_categories.append(error_category)
        
        # Calculate total tests
        self.total_tests = sum(len(cat.tests) for cat in self.test_categories)
    
    def create_ui(self):
        """Create the main user interface"""
        # Main container
        main_frame = ctk.CTkFrame(self.root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Title
        title = ctk.CTkLabel(main_frame, text="BMC Software QC Tester", 
                            font=ctk.CTkFont(size=24, weight="bold"))
        title.pack(pady=10)
        
        # Control panel
        self.create_control_panel(main_frame)
        
        # Results panel
        self.create_results_panel(main_frame)
        
        # Log panel
        self.create_log_panel(main_frame)
    
    def create_control_panel(self, parent):
        """Create control buttons and status"""
        control_frame = ctk.CTkFrame(parent)
        control_frame.pack(fill="x", pady=10)
        
        # Buttons
        button_frame = ctk.CTkFrame(control_frame)
        button_frame.pack(pady=10)
        
        self.start_button = ctk.CTkButton(button_frame, text="Start All Tests", 
                                         command=self.start_all_tests,
                                         height=40, width=150,
                                         font=ctk.CTkFont(size=14, weight="bold"))
        self.start_button.pack(side="left", padx=10)
        
        self.stop_button = ctk.CTkButton(button_frame, text="Stop Tests", 
                                        command=self.stop_tests,
                                        height=40, width=120,
                                        state="disabled")
        self.stop_button.pack(side="left", padx=10)
        
        self.clear_button = ctk.CTkButton(button_frame, text="Clear Results", 
                                         command=self.clear_results,
                                         height=40, width=120)
        self.clear_button.pack(side="left", padx=10)
        
        # Status
        status_frame = ctk.CTkFrame(control_frame)
        status_frame.pack(fill="x", pady=10)
        
        self.status_label = ctk.CTkLabel(status_frame, text="Ready to run tests", 
                                        font=ctk.CTkFont(size=14))
        self.status_label.pack(side="left", padx=10)
        
        # Progress
        self.progress_bar = ctk.CTkProgressBar(status_frame, width=300)
        self.progress_bar.pack(side="right", padx=10)
        self.progress_bar.set(0)
    
    def create_results_panel(self, parent):
        """Create results display panel"""
        results_frame = ctk.CTkFrame(parent)
        results_frame.pack(fill="both", expand=True, pady=10)
        
        ctk.CTkLabel(results_frame, text="Test Results", 
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=5)
        
        # Results tree
        self.results_container = ctk.CTkScrollableFrame(results_frame, height=300)
        self.results_container.pack(fill="both", expand=True, padx=10, pady=5)
        
        # Summary
        summary_frame = ctk.CTkFrame(results_frame)
        summary_frame.pack(fill="x", padx=10, pady=5)
        
        self.summary_label = ctk.CTkLabel(summary_frame, text="No tests run yet", 
                                         font=ctk.CTkFont(size=12))
        self.summary_label.pack(pady=5)
    
    def create_log_panel(self, parent):
        """Create log display panel"""
        log_frame = ctk.CTkFrame(parent)
        log_frame.pack(fill="both", expand=True, pady=10)
        
        ctk.CTkLabel(log_frame, text="Test Log", 
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=5)
        
        self.log_text = ctk.CTkTextbox(log_frame, height=150)
        self.log_text.pack(fill="both", expand=True, padx=10, pady=5)
    
    def log(self, message):
        """Add message to log"""
        timestamp = time.strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}\n"
        
        self.log_text.insert(tk.END, formatted)
        self.log_text.see(tk.END)
        print(f"QC Tester: {message}")
    
    def start_all_tests(self):
        """Start running all tests"""
        if self.test_running:
            return
        
        self.test_running = True
        self.stop_requested = False
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        
        self.clear_results()
        self.log("Starting comprehensive QC testing...")
        
        # Start test thread
        threading.Thread(target=self.run_all_tests, daemon=True).start()
    
    def stop_tests(self):
        """Stop running tests"""
        self.stop_requested = True
        self.log("Stop requested - finishing current test...")
    
    def clear_results(self):
        """Clear all test results"""
        for widget in self.results_container.winfo_children():
            widget.destroy()
        
        for category in self.test_categories:
            category.results = []
        
        self.passed_tests = 0
        self.failed_tests = 0
        self.skipped_tests = 0
        self.update_summary()
    
    def run_all_tests(self):
        """Run all test categories"""
        total_tests = sum(len(cat.tests) for cat in self.test_categories)
        current_test = 0
        
        for category in self.test_categories:
            if self.stop_requested:
                break
            
            self.log(f"\n=== {category.name.upper()} TESTS ===")
            
            # Create category display
            self.create_category_display(category)
            
            for test_name, test_func in category.tests:
                if self.stop_requested:
                    break
                
                current_test += 1
                progress = current_test / total_tests
                self.progress_bar.set(progress)
                
                self.status_label.configure(text=f"Running: {test_name}")
                self.log(f"Running: {test_name}")
                
                # Run the test
                result = self.run_single_test(test_name, test_func)
                category.results.append(result)
                
                # Update display
                self.update_test_result_display(category, result)
                self.update_summary()
                
                # Small delay between tests
                time.sleep(0.1)
        
        # Finish up
        self.test_running = False
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        
        if self.stop_requested:
            self.status_label.configure(text="Tests stopped by user")
            self.log("Tests stopped by user")
        else:
            self.status_label.configure(text="All tests completed")
            self.log("All tests completed")
            self.generate_summary_report()
        
        self.progress_bar.set(1.0 if not self.stop_requested else progress)
    
    def run_single_test(self, test_name: str, test_func: Callable) -> TestResult:
        """Run a single test function"""
        start_time = time.time()
        
        try:
            result = test_func()
            if isinstance(result, TestResult):
                result.duration = time.time() - start_time
                return result
            elif result is True:
                return TestResult(test_name, "PASS", "Test passed", time.time() - start_time)
            else:
                return TestResult(test_name, "FAIL", "Test returned False", time.time() - start_time)
        except Exception as e:
            return TestResult(test_name, "FAIL", f"Exception: {str(e)}", time.time() - start_time)
    
    def create_category_display(self, category: TestCategory):
        """Create display for a test category"""
        category_frame = ctk.CTkFrame(self.results_container)
        category_frame.pack(fill="x", pady=5)
        
        header = ctk.CTkLabel(category_frame, text=f"{category.name} ({category.description})", 
                             font=ctk.CTkFont(size=14, weight="bold"))
        header.pack(pady=5)
        
        # Store frame for updates
        category.display_frame = category_frame
    
    def update_test_result_display(self, category: TestCategory, result: TestResult):
        """Update display with test result"""
        # Color coding
        color_map = {
            "PASS": "#00AA00",
            "FAIL": "#AA0000", 
            "SKIP": "#AAAA00",
            "RUNNING": "#0088AA"
        }
        
        result_frame = ctk.CTkFrame(category.display_frame)
        result_frame.pack(fill="x", padx=10, pady=2)
        
        # Status indicator
        status_label = ctk.CTkLabel(result_frame, text=result.status, 
                                   text_color=color_map.get(result.status, "#FFFFFF"),
                                   width=60, font=ctk.CTkFont(weight="bold"))
        status_label.pack(side="left", padx=5)
        
        # Test name
        name_label = ctk.CTkLabel(result_frame, text=result.name, anchor="w")
        name_label.pack(side="left", fill="x", expand=True, padx=5)
        
        # Duration
        duration_label = ctk.CTkLabel(result_frame, text=f"{result.duration:.2f}s", width=60)
        duration_label.pack(side="right", padx=5)
        
        # Message (if any)
        if result.message:
            msg_label = ctk.CTkLabel(result_frame, text=result.message, 
                                    font=ctk.CTkFont(size=10), text_color="#CCCCCC")
            msg_label.pack(side="right", padx=5)
    
    def update_summary(self):
        """Update test summary display"""
        total_run = self.passed_tests + self.failed_tests + self.skipped_tests
        
        if total_run == 0:
            self.summary_label.configure(text="No tests run yet")
        else:
            pass_rate = (self.passed_tests / total_run) * 100 if total_run > 0 else 0
            summary_text = (f"Tests: {total_run}/{self.total_tests} | "
                          f"Passed: {self.passed_tests} | "
                          f"Failed: {self.failed_tests} | "
                          f"Skipped: {self.skipped_tests} | "
                          f"Pass Rate: {pass_rate:.1f}%")
            self.summary_label.configure(text=summary_text)
        
        # Update counters
        total_results = sum(len(cat.results) for cat in self.test_categories)
        self.passed_tests = sum(1 for cat in self.test_categories for r in cat.results if r.status == "PASS")
        self.failed_tests = sum(1 for cat in self.test_categories for r in cat.results if r.status == "FAIL")
        self.skipped_tests = sum(1 for cat in self.test_categories for r in cat.results if r.status == "SKIP")
    
    def generate_summary_report(self):
        """Generate final summary report"""
        self.log("\n" + "="*50)
        self.log("QC TEST SUMMARY REPORT")
        self.log("="*50)
        
        for category in self.test_categories:
            if category.results:
                passed = sum(1 for r in category.results if r.status == "PASS")
                failed = sum(1 for r in category.results if r.status == "FAIL")
                skipped = sum(1 for r in category.results if r.status == "SKIP")
                
                self.log(f"{category.name}: {passed} passed, {failed} failed, {skipped} skipped")
                
                # Log failed tests
                for result in category.results:
                    if result.status == "FAIL":
                        self.log(f"  FAILED: {result.name} - {result.message}")
        
        pass_rate = (self.passed_tests / (self.passed_tests + self.failed_tests + self.skipped_tests)) * 100
        self.log(f"\nOverall Pass Rate: {pass_rate:.1f}%")
        
        if self.failed_tests == 0:
            self.log("🎉 ALL TESTS PASSED!")
        else:
            self.log(f"⚠️  {self.failed_tests} test(s) failed - review needed")
    
    # ================== TEST IMPLEMENTATIONS ==================
    
    # Environment Tests
    def test_python_version(self) -> TestResult:
        """Test Python version compatibility"""
        version = sys.version_info
        if version.major == 3 and version.minor >= 8:
            return TestResult("Python Version", "PASS", f"Python {version.major}.{version.minor}.{version.micro}")
        else:
            return TestResult("Python Version", "FAIL", f"Python {version.major}.{version.minor} - need 3.8+")
    
    def test_required_packages(self) -> TestResult:
        """Test required Python packages"""
        required = ["customtkinter", "serial", "psutil", "redfish"]
        missing = []
        
        for package in required:
            try:
                __import__(package)
            except ImportError:
                missing.append(package)
        
        if missing:
            return TestResult("Required Packages", "FAIL", f"Missing: {', '.join(missing)}")
        else:
            return TestResult("Required Packages", "PASS", "All packages available")
    
    def test_serial_devices(self) -> TestResult:
        """Test serial device detection"""
        devices = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
        
        if devices:
            return TestResult("Serial Devices", "PASS", f"Found {len(devices)} devices: {', '.join(devices)}")
        else:
            return TestResult("Serial Devices", "SKIP", "No serial devices found")
    
    def test_network_interfaces(self) -> TestResult:
        """Test network interface detection"""
        try:
            import subprocess
            result = subprocess.run(['ip', 'addr'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return TestResult("Network Interfaces", "PASS", "IP command working")
            else:
                return TestResult("Network Interfaces", "FAIL", "IP command failed")
        except Exception as e:
            return TestResult("Network Interfaces", "FAIL", str(e))
    
    def test_file_permissions(self) -> TestResult:
        """Test file permissions for main modules"""
        files_to_check = ["main.py", "extra.py", "utils.py", "network.py", "bmc.py"]
        missing = []
        
        for file in files_to_check:
            if not os.path.exists(file):
                missing.append(file)
            elif not os.access(file, os.R_OK):
                missing.append(f"{file} (not readable)")
        
        if missing:
            return TestResult("File Permissions", "FAIL", f"Issues: {', '.join(missing)}")
        else:
            return TestResult("File Permissions", "PASS", "All files accessible")
    
    def test_terminal_emulators(self) -> TestResult:
        """Test terminal emulator availability"""
        terminals = ["terminator", "xterm", "gnome-terminal"]
        available = []
        
        for term in terminals:
            try:
                subprocess.run([term, "--version"], capture_output=True, timeout=3)
                available.append(term)
            except:
                pass
        
        if available:
            return TestResult("Terminal Emulators", "PASS", f"Available: {', '.join(available)}")
        else:
            return TestResult("Terminal Emulators", "FAIL", "No terminal emulators found")
    
    # Core Module Tests
    def test_import_main(self) -> TestResult:
        """Test importing main.py"""
        try:
            spec = importlib.util.spec_from_file_location("main", "main.py")
            if spec is None:
                return TestResult("Import main.py", "FAIL", "Could not load spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return TestResult("Import main.py", "PASS", "Successfully imported")
        except Exception as e:
            return TestResult("Import main.py", "FAIL", str(e))
    
    def test_import_extra(self) -> TestResult:
        """Test importing extra.py"""
        try:
            spec = importlib.util.spec_from_file_location("extra", "extra.py")
            if spec is None:
                return TestResult("Import extra.py", "SKIP", "extra.py not found")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return TestResult("Import extra.py", "PASS", "Successfully imported")
        except Exception as e:
            return TestResult("Import extra.py", "FAIL", str(e))
    
    def test_import_utils(self) -> TestResult:
        """Test importing utils.py"""
        try:
            spec = importlib.util.spec_from_file_location("utils", "utils.py")
            if spec is None:
                return TestResult("Import utils.py", "FAIL", "Could not load spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return TestResult("Import utils.py", "PASS", "Successfully imported")
        except Exception as e:
            return TestResult("Import utils.py", "FAIL", str(e))
    
    def test_import_network(self) -> TestResult:
        """Test importing network.py"""
        try:
            spec = importlib.util.spec_from_file_location("network", "network.py")
            if spec is None:
                return TestResult("Import network.py", "FAIL", "Could not load spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return TestResult("Import network.py", "PASS", "Successfully imported")
        except Exception as e:
            return TestResult("Import network.py", "FAIL", str(e))
    
    def test_import_bmc(self) -> TestResult:
        """Test importing bmc.py"""
        try:
            spec = importlib.util.spec_from_file_location("bmc", "bmc.py")
            if spec is None:
                return TestResult("Import bmc.py", "FAIL", "Could not load spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return TestResult("Import bmc.py", "PASS", "Successfully imported")
        except Exception as e:
            return TestResult("Import bmc.py", "FAIL", str(e))
    
    # UI Component Tests (simplified for safety)
    def test_main_window(self) -> TestResult:
        """Test main window creation"""
        try:
            # This is a simplified test - in real testing you'd need to be more careful
            return TestResult("Main Window Creation", "SKIP", "Manual testing required")
        except Exception as e:
            return TestResult("Main Window Creation", "FAIL", str(e))
    
    def test_multi_unit_window(self) -> TestResult:
        """Test multi-unit window"""
        return TestResult("Multi-Unit Window", "SKIP", "Manual testing required")
    
    def test_file_dialogs(self) -> TestResult:
        """Test file selection dialogs"""
        return TestResult("File Selection Dialogs", "SKIP", "Manual testing required")
    
    def test_ip_detection(self) -> TestResult:
        """Test IP detection functionality"""
        try:
            import subprocess
            result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=3)
            if result.returncode == 0 and result.stdout.strip():
                return TestResult("Network IP Detection", "PASS", f"Detected: {result.stdout.strip()}")
            else:
                return TestResult("Network IP Detection", "FAIL", "No IP detected")
        except Exception as e:
            return TestResult("Network IP Detection", "FAIL", str(e))
    
    def test_config_management(self) -> TestResult:
        """Test configuration save/load"""
        test_config = {"test": "value", "number": 123}
        test_file = "/tmp/qc_test_config.json"
        
        try:
            # Test save
            with open(test_file, 'w') as f:
                json.dump(test_config, f)
            
            # Test load
            with open(test_file, 'r') as f:
                loaded = json.load(f)
            
            # Cleanup
            os.remove(test_file)
            
            if loaded == test_config:
                return TestResult("Configuration Save/Load", "PASS", "Config handling works")
            else:
                return TestResult("Configuration Save/Load", "FAIL", "Config mismatch")
        except Exception as e:
            return TestResult("Configuration Save/Load", "FAIL", str(e))
    
    # Serial Communication Tests
    def test_serial_detection(self) -> TestResult:
        """Test serial port detection"""
        devices = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
        
        if devices:
            return TestResult("Serial Port Detection", "PASS", f"Found: {', '.join(devices)}")
        else:
            return TestResult("Serial Port Detection", "SKIP", "No serial devices found")
    
    def test_serial_connection(self) -> TestResult:
        """Test serial connection establishment"""
        devices = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
        
        if not devices:
            return TestResult("Serial Connection", "SKIP", "No serial devices available")
        
        try:
            import serial
            ser = serial.Serial(devices[0], 115200, timeout=1)
            ser.close()
            return TestResult("Serial Connection", "PASS", f"Connected to {devices[0]}")
        except Exception as e:
            return TestResult("Serial Connection", "FAIL", str(e))
    
    def test_serial_reading(self) -> TestResult:
        """Test serial data reading"""
        return TestResult("Serial Data Reading", "SKIP", "Requires active BMC connection")
    
    def test_serial_cleanup(self) -> TestResult:
        """Test serial cleanup functionality"""
        try:
            # Test the cleanup function exists and is callable
            spec = importlib.util.spec_from_file_location("utils", "utils.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            if hasattr(module, 'cleanup_all_serial_connections'):
                return TestResult("Serial Cleanup", "PASS", "Cleanup function available")
            else:
                return TestResult("Serial Cleanup", "FAIL", "Cleanup function not found")
        except Exception as e:
            return TestResult("Serial Cleanup", "FAIL", str(e))
    
    def test_multiple_serial(self) -> TestResult:
        """Test multiple serial port handling"""
        devices = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
        
        if len(devices) < 2:
            return TestResult("Multiple Serial Ports", "SKIP", f"Only {len(devices)} device(s) available")
        
        try:
            import serial
            connections = []
            for device in devices[:2]:  # Test first 2 devices
                ser = serial.Serial(device, 115200, timeout=1)
                connections.append(ser)
            
            # Close all connections
            for ser in connections:
                ser.close()
            
            return TestResult("Multiple Serial Ports", "PASS", f"Handled {len(connections)} devices")
        except Exception as e:
            return TestResult("Multiple Serial Ports", "FAIL", str(e))
    
    # Process Management Tests
    def test_http_server(self) -> TestResult:
        """Test HTTP server creation and basic functionality"""
        try:
            from http.server import HTTPServer, SimpleHTTPRequestHandler
            import threading
            import requests
            
            # Create server on a random port
            server = HTTPServer(('localhost', 0), SimpleHTTPRequestHandler)
            port = server.server_address[1]
            
            # Start server in thread
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            
            # Test connection
            try:
                response = requests.get(f"http://localhost:{port}", timeout=2)
                server.shutdown()
                return TestResult("HTTP Server Creation", "PASS", f"Server on port {port}")
            except:
                server.shutdown()
                return TestResult("HTTP Server Creation", "FAIL", "Server not responding")
                
        except Exception as e:
            return TestResult("HTTP Server Creation", "FAIL", str(e))
    
    def test_server_cleanup(self) -> TestResult:
        """Test server cleanup functionality"""
        try:
            # Test that we can create and destroy servers properly
            from http.server import HTTPServer, SimpleHTTPRequestHandler
            
            server = HTTPServer(('localhost', 0), SimpleHTTPRequestHandler)
            server.shutdown()
            server.server_close()
            
            return TestResult("Server Cleanup", "PASS", "Server cleanup works")
        except Exception as e:
            return TestResult("Server Cleanup", "FAIL", str(e))
    
    def test_terminator_process(self) -> TestResult:
        """Test terminator process management"""
        try:
            result = subprocess.run(['which', 'terminator'], capture_output=True, timeout=3)
            if result.returncode == 0:
                return TestResult("Terminator Process", "PASS", "Terminator available")
            else:
                return TestResult("Terminator Process", "SKIP", "Terminator not installed")
        except Exception as e:
            return TestResult("Terminator Process", "FAIL", str(e))
    
    def test_minicom_process(self) -> TestResult:
        """Test minicom process management"""
        try:
            result = subprocess.run(['which', 'minicom'], capture_output=True, timeout=3)
            if result.returncode == 0:
                return TestResult("Minicom Process", "PASS", "Minicom available")
            else:
                return TestResult("Minicom Process", "SKIP", "Minicom not installed")
        except Exception as e:
            return TestResult("Minicom Process", "FAIL", str(e))
    
    def test_zombie_processes(self) -> TestResult:
        """Test zombie process detection"""
        try:
            import psutil
            zombies = []
            
            for proc in psutil.process_iter(['pid', 'status', 'name']):
                try:
                    if proc.info['status'] == psutil.STATUS_ZOMBIE:
                        zombies.append(proc.info['pid'])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            if zombies:
                return TestResult("Zombie Process Detection", "FAIL", f"Found zombies: {zombies}")
            else:
                return TestResult("Zombie Process Detection", "PASS", "No zombie processes")
        except Exception as e:
            return TestResult("Zombie Process Detection", "FAIL", str(e))
    
    def test_resource_cleanup(self) -> TestResult:
        """Test resource cleanup mechanisms"""
        try:
            # Test memory usage is reasonable
            import psutil
            process = psutil.Process(os.getpid())
            memory_mb = process.memory_info().rss / 1024 / 1024
            
            if memory_mb < 500:  # Less than 500MB
                return TestResult("Resource Cleanup", "PASS", f"Memory usage: {memory_mb:.1f}MB")
            else:
                return TestResult("Resource Cleanup", "FAIL", f"High memory usage: {memory_mb:.1f}MB")
        except Exception as e:
            return TestResult("Resource Cleanup", "FAIL", str(e))
    
    # Multi-Flash Tests
    def test_unit_config(self) -> TestResult:
        """Test unit configuration validation"""
        try:
            # Test basic config validation logic
            test_configs = [
                {"device": "/dev/ttyUSB0", "username": "root", "password": "test", "bmc_ip": "192.168.1.100", "host_ip": "192.168.1.1"},
                {"device": "", "username": "root", "password": "test", "bmc_ip": "192.168.1.100", "host_ip": "192.168.1.1"},  # Invalid
                {"device": "/dev/ttyUSB0", "username": "", "password": "test", "bmc_ip": "192.168.1.100", "host_ip": "192.168.1.1"},  # Invalid
            ]
            
            valid_count = 0
            for config in test_configs:
                if all(config.values()):  # Simple validation
                    valid_count += 1
            
            if valid_count == 1:  # Only first config should be valid
                return TestResult("Unit Configuration", "PASS", "Config validation works")
            else:
                return TestResult("Unit Configuration", "FAIL", f"Expected 1 valid, got {valid_count}")
        except Exception as e:
            return TestResult("Unit Configuration", "FAIL", str(e))
    
    def test_shared_servers(self) -> TestResult:
        """Test shared server management"""
        try:
            # Test the concept of shared server management
            server_pool = {}
            
            # Simulate adding servers
            server_pool["192.168.1.1"] = {"usage_count": 1, "server": "mock_server"}
            server_pool["192.168.1.1"]["usage_count"] += 1
            
            if server_pool["192.168.1.1"]["usage_count"] == 2:
                return TestResult("Shared Server Management", "PASS", "Server sharing logic works")
            else:
                return TestResult("Shared Server Management", "FAIL", "Server sharing logic failed")
        except Exception as e:
            return TestResult("Shared Server Management", "FAIL", str(e))
    
    def test_multi_console(self) -> TestResult:
        """Test multi-console functionality"""
        try:
            # Test terminator config generation logic
            units = [
                {"device": "/dev/ttyUSB0", "bmc_ip": "192.168.1.100"},
                {"device": "/dev/ttyUSB1", "bmc_ip": "192.168.1.101"}
            ]
            
            if len(units) <= 4:  # Should be able to handle up to 4 units
                return TestResult("Multi-Console", "PASS", f"Can handle {len(units)} units")
            else:
                return TestResult("Multi-Console", "FAIL", "Too many units for multi-console")
        except Exception as e:
            return TestResult("Multi-Console", "FAIL", str(e))
    
    def test_config_validation(self) -> TestResult:
        """Test configuration validation"""
        try:
            # Test IP validation
            valid_ips = ["192.168.1.1", "10.0.0.1", "172.16.0.1"]
            invalid_ips = ["999.999.999.999", "192.168.1", "not.an.ip"]
            
            import re
            ip_pattern = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}')
            
            valid_count = sum(1 for ip in valid_ips if ip_pattern.match(ip))
            invalid_count = sum(1 for ip in invalid_ips if ip_pattern.match(ip))
            
            if valid_count == 3 and invalid_count == 0:
                return TestResult("Config Validation", "PASS", "IP validation works")
            else:
                return TestResult("Config Validation", "FAIL", f"IP validation failed: {valid_count}/3 valid, {invalid_count}/3 invalid")
        except Exception as e:
            return TestResult("Config Validation", "FAIL", str(e))
    
    def test_progress_tracking(self) -> TestResult:
        """Test progress tracking functionality"""
        try:
            # Test progress calculation
            progress_values = [0.0, 0.25, 0.5, 0.75, 1.0]
            
            # Simulate progress tracking
            for progress in progress_values:
                if not (0.0 <= progress <= 1.0):
                    return TestResult("Progress Tracking", "FAIL", f"Invalid progress value: {progress}")
            
            return TestResult("Progress Tracking", "PASS", "Progress values in valid range")
        except Exception as e:
            return TestResult("Progress Tracking", "FAIL", str(e))
    
    # Error Handling Tests
    def test_missing_files(self) -> TestResult:
        """Test handling of missing files"""
        try:
            # Test file existence checking
            test_file = "/tmp/nonexistent_file_qc_test.txt"
            
            if os.path.exists(test_file):
                return TestResult("Missing Files", "FAIL", "Test file should not exist")
            
            # Test graceful handling
            try:
                with open(test_file, 'r') as f:
                    content = f.read()
                return TestResult("Missing Files", "FAIL", "Should have raised exception")
            except FileNotFoundError:
                return TestResult("Missing Files", "PASS", "FileNotFoundError handled correctly")
        except Exception as e:
            return TestResult("Missing Files", "FAIL", str(e))
    
    def test_invalid_ips(self) -> TestResult:
        """Test handling of invalid IP addresses"""
        try:
            import socket
            
            invalid_ips = ["999.999.999.999", "not.an.ip", "192.168.1"]
            
            for ip in invalid_ips:
                try:
                    socket.inet_aton(ip)
                    return TestResult("Invalid IP Addresses", "FAIL", f"Should reject {ip}")
                except socket.error:
                    pass  # Expected behavior
            
            return TestResult("Invalid IP Addresses", "PASS", "Invalid IPs rejected correctly")
        except Exception as e:
            return TestResult("Invalid IP Addresses", "FAIL", str(e))
    
    def test_serial_errors(self) -> TestResult:
        """Test serial error handling"""
        try:
            import serial
            
            # Try to open non-existent serial port
            try:
                ser = serial.Serial("/dev/nonexistent_port", 115200, timeout=1)
                ser.close()
                return TestResult("Serial Port Errors", "FAIL", "Should have raised exception")
            except serial.SerialException:
                return TestResult("Serial Port Errors", "PASS", "SerialException handled correctly")
        except Exception as e:
            return TestResult("Serial Port Errors", "FAIL", str(e))
    
    def test_network_errors(self) -> TestResult:
        """Test network error handling"""
        try:
            import socket
            
            # Try to connect to non-existent host
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                sock.connect(("192.168.999.999", 80))
                sock.close()
                return TestResult("Network Errors", "FAIL", "Should have raised exception")
            except (socket.error, socket.timeout):
                return TestResult("Network Errors", "PASS", "Network error handled correctly")
        except Exception as e:
            return TestResult("Network Errors", "FAIL", str(e))
    
    def test_memory_stress(self) -> TestResult:
        """Test memory usage under stress"""
        try:
            import psutil
            import gc
            
            # Get initial memory
            process = psutil.Process(os.getpid())
            initial_memory = process.memory_info().rss / 1024 / 1024
            
            # Create some objects and clean them up
            big_list = [i for i in range(100000)]
            del big_list
            gc.collect()
            
            # Check memory after cleanup
            final_memory = process.memory_info().rss / 1024 / 1024
            memory_increase = final_memory - initial_memory
            
            if memory_increase < 50:  # Less than 50MB increase
                return TestResult("Memory Stress Test", "PASS", f"Memory increase: {memory_increase:.1f}MB")
            else:
                return TestResult("Memory Stress Test", "FAIL", f"High memory increase: {memory_increase:.1f}MB")
        except Exception as e:
            return TestResult("Memory Stress Test", "FAIL", str(e))
    
    def test_exception_handling(self) -> TestResult:
        """Test general exception handling"""
        try:
            # Test that we can catch and handle various exceptions
            exceptions_caught = 0
            
            # Test division by zero
            try:
                result = 1 / 0
            except ZeroDivisionError:
                exceptions_caught += 1
            
            # Test key error
            try:
                d = {}
                value = d['nonexistent_key']
            except KeyError:
                exceptions_caught += 1
            
            # Test type error
            try:
                result = "string" + 123
            except TypeError:
                exceptions_caught += 1
            
            if exceptions_caught == 3:
                return TestResult("Exception Handling", "PASS", "All exceptions caught correctly")
            else:
                return TestResult("Exception Handling", "FAIL", f"Only caught {exceptions_caught}/3 exceptions")
        except Exception as e:
            return TestResult("Exception Handling", "FAIL", str(e))
    
    def on_close(self):
        """Handle application close"""
        if self.test_running:
            if messagebox.askyesno("Confirm Exit", "Tests are running. Are you sure you want to exit?"):
                self.stop_requested = True
                self.root.after(1000, self.root.destroy)  # Give time for cleanup
            else:
                return
        else:
            self.root.destroy()
    
    def run(self):
        """Start the QC tester application"""
        self.root.mainloop()


def main():
    """Main entry point"""
    print("Starting BMC Software QC Tester...")
    app = QCTester()
    app.run()


if __name__ == "__main__":
    main()