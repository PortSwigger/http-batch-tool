# -*- coding: utf-8 -*-
from burp import IBurpExtender, ITab, IExtensionStateListener, IHttpRequestResponse, IMessageEditorController
from java.awt import BorderLayout, Color, Font, Dimension
from java.awt.event import MouseAdapter, MouseEvent
from java.awt.datatransfer import DataFlavor
from javax.swing import JPanel, JTextArea, JButton, JScrollPane, JPopupMenu, JMenuItem, JTable, JSplitPane, JLabel, SwingConstants, JTabbedPane
from javax.swing.table import DefaultTableModel
from java.net import URL, MalformedURLException, InetAddress, UnknownHostException
from java.util.concurrent import Executors, TimeUnit
from java.lang import Runnable
from threading import Lock
import time
import re

class BurpExtender(IBurpExtender, ITab, IExtensionStateListener):
    def registerExtenderCallbacks(self, callbacks):
        self._callbacks = callbacks
        self._helpers = callbacks.getHelpers()
        self._callbacks.setExtensionName("HTTP Batch Tool")

        # Thread pool
        self.executor = Executors.newFixedThreadPool(10)
        self.lock = Lock()
        self.request_count = 0
        self.completed_count = 0
        self.is_running = False  # Flag to prevent multiple executions
        
        # Store request/response data
        self.request_responses = {}  # url -> IHttpRequestResponse

        # Register listener
        self._callbacks.registerExtensionStateListener(self)

        # UI Setup
        self.setup_ui()
        self._callbacks.addSuiteTab(self)

    def setup_ui(self):
        # Main panel
        self.panel = JPanel(BorderLayout())
        
        # Create main split pane (horizontal)
        main_split = JSplitPane(JSplitPane.HORIZONTAL_SPLIT)
        
        # Left panel - URL input and results table
        left_panel = JPanel(BorderLayout())
        
        # Create vertical split pane for input and results
        left_split = JSplitPane(JSplitPane.VERTICAL_SPLIT)
        
        # Top panel for URL input
        top_panel = JPanel(BorderLayout())
        
        # URL input area
        self.text_area = JTextArea(8, 40)
        self.text_area.setFont(Font("Monospaced", Font.PLAIN, 12))
        
        # Popup menu for paste functionality
        popup_menu = JPopupMenu()
        paste_item = JMenuItem("Paste URLs", actionPerformed=self.paste_urls)
        popup_menu.add(paste_item)
        self.text_area.setComponentPopupMenu(popup_menu)
        
        top_panel.add(JScrollPane(self.text_area), BorderLayout.CENTER)
        
        # Button panel
        button_panel = JPanel()
        self.send_button = JButton("Send Requests")
        # Remove any existing action listeners to prevent duplicates
        for listener in self.send_button.getActionListeners():
            self.send_button.removeActionListener(listener)
        # Add our action listener
        self.send_button.addActionListener(self.on_send_requests)
        
        # Set Burp orange color
        self.send_button.setBackground(Color(255, 102, 51))  # #FF6633
        self.send_button.setForeground(Color.WHITE)
        self.send_button.setFont(Font("SansSerif", Font.BOLD, 12))
        self.send_button.setPreferredSize(Dimension(150, 30))
        button_panel.add(self.send_button)
        
        # Status label
        self.status_label = JLabel("Ready to send requests")
        self.status_label.setHorizontalAlignment(SwingConstants.CENTER)
        self.status_label.setFont(Font("SansSerif", Font.PLAIN, 11))
        button_panel.add(self.status_label)
        
        top_panel.add(button_panel, BorderLayout.SOUTH)
        
        # Bottom panel for results table
        table_panel = JPanel(BorderLayout())
        
        # Results table
        self.table_model = DefaultTableModel()
        self.table_model.addColumn("URL")
        self.table_model.addColumn("Status")
        self.table_model.addColumn("Length")
        self.table_model.addColumn("Time")
        self.table_model.addColumn("Error Details")
        
        self.results_table = JTable(self.table_model)
        self.results_table.setAutoCreateRowSorter(True)
        self.results_table.getColumnModel().getColumn(0).setPreferredWidth(300)
        self.results_table.getColumnModel().getColumn(1).setPreferredWidth(60)
        self.results_table.getColumnModel().getColumn(2).setPreferredWidth(60)
        self.results_table.getColumnModel().getColumn(3).setPreferredWidth(80)
        self.results_table.getColumnModel().getColumn(4).setPreferredWidth(150)
        
        # Add mouse listener for table row selection
        self.results_table.addMouseListener(TableMouseListener(self))
        
        results_scroll = JScrollPane(self.results_table)
        results_scroll.setPreferredSize(Dimension(500, 250))
        
        table_panel.add(JLabel("Request Results (click row to view details):"), BorderLayout.NORTH)
        table_panel.add(results_scroll, BorderLayout.CENTER)
        
        # Configure left split pane
        left_split.setTopComponent(top_panel)
        left_split.setBottomComponent(table_panel)
        left_split.setDividerLocation(160)
        left_split.setResizeWeight(0.3)
        
        left_panel.add(left_split, BorderLayout.CENTER)
        
        # Right panel - Request/Response viewer
        right_panel = self.create_message_viewer()
        
        # Configure main split pane
        main_split.setLeftComponent(left_panel)
        main_split.setRightComponent(right_panel)
        main_split.setDividerLocation(520)
        main_split.setResizeWeight(0.5)
        
        self.panel.add(main_split, BorderLayout.CENTER)

    def create_message_viewer(self):
        """Create the request/response message viewer panel"""
        viewer_panel = JPanel(BorderLayout())
        viewer_panel.add(JLabel("HTTP Messages (select a request from the table)"), BorderLayout.NORTH)
        
        # Create tabbed pane for request/response
        self.message_tabs = JTabbedPane()
        
        # Request tab
        self.request_viewer = self._callbacks.createMessageEditor(MessageEditorController(self), False)
        self.message_tabs.addTab("Request", self.request_viewer.getComponent())
        
        # Response tab  
        self.response_viewer = self._callbacks.createMessageEditor(MessageEditorController(self), False)
        self.message_tabs.addTab("Response", self.response_viewer.getComponent())
        
        viewer_panel.add(self.message_tabs, BorderLayout.CENTER)
        
        return viewer_panel

    def getTabCaption(self):
        return u"HTTP Batch Tool"

    def getUiComponent(self):
        return self.panel

    def validate_url_format(self, url):
        """Validate URL format using regex"""
        url_pattern = re.compile(
            r'^https?://'  # http:// ou https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
            r'localhost|'  # localhost
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP
            r'(?::\d+)?'  # optional port
            r'(?:/?|[/?]\S+)$', re.IGNORECASE)
        return url_pattern.match(url) is not None

    def test_dns_resolution(self, host):
        """Test DNS resolution of the host"""
        try:
            address = InetAddress.getByName(host)
            self._callbacks.printOutput(u"DNS resolved: {} -> {}".format(host, address.getHostAddress()))
            return True, None
        except UnknownHostException as e:
            error_msg = "Host not found (DNS resolution failed)"
            self._callbacks.printError(u"DNS resolution failed for {}: {}".format(host, unicode(e)))
            return False, error_msg
        except Exception as e:
            error_msg = "DNS error: {}".format(unicode(e))
            self._callbacks.printError(u"DNS test error for {}: {}".format(host, unicode(e)))
            return False, error_msg

    def validate_and_process_urls(self):
        """Validate and process URLs before sending requests"""
        raw_text = self.text_area.getText()
        if not raw_text.strip():
            self._callbacks.printOutput(u"No URLs provided.")
            self.update_status("No URLs provided")
            return []

        urls = raw_text.strip().split('\n')
        valid_urls = []
        invalid_count = 0
        
        from javax.swing import SwingUtilities
        
        for url in urls:
            url = url.strip()
            if not url:
                continue
                
            if not url.startswith(("http://", "https://")):
                self._callbacks.printError(u"URL must start with http:// or https://: {}".format(url))
                SwingUtilities.invokeLater(lambda u=url: self.add_error_to_table(u, "INVALID PROTOCOL", "Must start with http:// or https://"))
                invalid_count += 1
                continue
            
            if not self.validate_url_format(url):
                self._callbacks.printError(u"Invalid URL format: {}".format(url))
                SwingUtilities.invokeLater(lambda u=url: self.add_error_to_table(u, "INVALID FORMAT", "Malformed URL"))
                invalid_count += 1
                continue
            
            try:
                parsed_url = URL(url)
                host = parsed_url.getHost()
                
                dns_ok, dns_error = self.test_dns_resolution(host)
                if not dns_ok:
                    self._callbacks.printError(u"DNS resolution failed for: {}".format(url))
                    SwingUtilities.invokeLater(lambda u=url, e=dns_error: self.add_error_to_table(u, "DNS ERROR", e))
                    invalid_count += 1
                    continue
                    
            except MalformedURLException as e:
                self._callbacks.printError(u"Malformed URL: {} - {}".format(url, unicode(e)))
                SwingUtilities.invokeLater(lambda u=url: self.add_error_to_table(u, "MALFORMED", "Invalid URL structure"))
                invalid_count += 1
                continue
            
            valid_urls.append(url)
            self._callbacks.printOutput(u"Valid URL: {}".format(url))
        
        if invalid_count > 0:
            self.update_status("Found {} valid URLs, {} invalid".format(len(valid_urls), invalid_count))
        
        return valid_urls

    def add_error_to_table(self, url, status, error_details):
        """Adicionar erro à tabela de resultados"""
        try:
            row = [url, status, "0", "0ms", error_details]
            self.table_model.addRow(row)
        except Exception as e:
            self._callbacks.printError(u"Error adding error row to table: {}".format(unicode(e)))

    def on_send_requests(self, event):
        # Prevent multiple simultaneous executions
        with self.lock:
            if self.is_running:
                return
            self.is_running = True
        
        try:
            # Clear previous results first
            self.table_model.setRowCount(0)
            with self.lock:
                self.request_responses.clear()
            
            # Clear message viewers
            empty_message = []
            self.request_viewer.setMessage(empty_message, True)
            self.response_viewer.setMessage(empty_message, False)
            
            # Validate and get URLs
            valid_urls = self.validate_and_process_urls()
            
            if not valid_urls:
                self.update_status("No valid URLs to process")
                with self.lock:
                    self.is_running = False
                return

            # Reset counters for valid URLs only
            self.request_count = len(valid_urls)
            self.completed_count = 0
            
            self._callbacks.printOutput(u"Starting {} HTTP requests...".format(self.request_count))
            self.update_status("Sending {} HTTP requests...".format(self.request_count))
            
            # Disable button during processing
            self.send_button.setEnabled(False)
            
            for url in valid_urls:
                self.executor.submit(RequestRunnable(self, url))
                
        except Exception as e:
            self._callbacks.printError(u"Exception in on_send_requests(): {}".format(unicode(e)))
            # Re-enable button and reset flag on error
            self.send_button.setEnabled(True)
            with self.lock:
                self.is_running = False

    def update_status(self, message):
        """Update the status label safely from any thread"""
        try:
            from javax.swing import SwingUtilities
            SwingUtilities.invokeLater(lambda: self.status_label.setText(message))
        except:
            pass  # Ignore threading issues with UI updates

    def add_result(self, url, status_code, response_length, duration_ms, request_response):
        """Add a successful result to the table (thread-safe)"""
        try:
            # Format duration
            if duration_ms < 1000:
                time_str = "{}ms".format(int(duration_ms))
            else:
                time_str = "{:.1f}s".format(duration_ms / 1000.0)
            
            # Store the request/response data
            with self.lock:
                self.request_responses[url] = request_response
            
            # Use SwingUtilities to ensure UI updates happen on EDT
            from javax.swing import SwingUtilities
            
            def update_table():
                try:
                    # Add row to table with empty error details for successful requests
                    row = [url, str(status_code), str(response_length), time_str, ""]
                    self.table_model.addRow(row)
                except Exception as e:
                    self._callbacks.printError(u"Error adding row to table: {}".format(unicode(e)))
            
            SwingUtilities.invokeLater(update_table)
            
            # Update completion count
            self.check_completion()
            
        except Exception as e:
            self._callbacks.printError(u"Error updating results: {}".format(unicode(e)))

    def request_failed(self, url, error_msg):
        """Handle failed HTTP requests"""
        try:
            from javax.swing import SwingUtilities
            
            def update_table():
                try:
                    row = [url, "ERROR", "0", "0ms", error_msg]
                    self.table_model.addRow(row)
                except Exception as e:
                    self._callbacks.printError(u"Error adding failed row to table: {}".format(unicode(e)))
            
            SwingUtilities.invokeLater(update_table)
            self.check_completion()
            
        except Exception as e:
            self._callbacks.printError(u"Error updating failed result: {}".format(unicode(e)))

    def check_completion(self):
        """Check if all requests are completed"""
        with self.lock:
            self.completed_count += 1
            if self.completed_count >= self.request_count:
                self.update_status("Completed {} requests".format(self.request_count))
                from javax.swing import SwingUtilities
                SwingUtilities.invokeLater(lambda: self.send_button.setEnabled(True))
                self._callbacks.printOutput(u"All {} HTTP requests completed. Results available in HTTP Batch Tool tab.".format(self.request_count))
                self.is_running = False  # Reset flag
            else:
                self.update_status("Completed {}/{} requests".format(self.completed_count, self.request_count))

    def on_table_selection(self, selected_row):
        """Handle table row selection to display request/response"""
        try:
            if selected_row >= 0 and selected_row < self.table_model.getRowCount():
                url = self.table_model.getValueAt(selected_row, 0)
                
                if url in self.request_responses:
                    request_response = self.request_responses[url]
                    
                    # Update message editors
                    self.request_viewer.setMessage(request_response.getRequest(), True)
                    self.response_viewer.setMessage(request_response.getResponse(), False)
                else:
                    # Clear viewers if no data available
                    empty_message = []
                    self.request_viewer.setMessage(empty_message, True)
                    self.response_viewer.setMessage(empty_message, False)
                    
        except Exception as e:
            self._callbacks.printError(u"Error displaying message: {}".format(unicode(e)))

    def fetch_url(self, url):
        """Make HTTP request to valid URL"""
        start_time = time.time()
        
        try:
            parsed_url = URL(url)
            host = parsed_url.getHost()
            port = parsed_url.getPort()
            protocol = parsed_url.getProtocol()
            path = parsed_url.getFile()

            if port == -1:
                port = 443 if protocol == "https" else 80

            # Build HTTP request
            request = self._helpers.buildHttpRequest(parsed_url)
            analyzed = self._helpers.analyzeRequest(request)
            headers = list(analyzed.getHeaders())
            body = request[analyzed.getBodyOffset():]

            # Update User-Agent
            headers = [h for h in headers if not h.lower().startswith("user-agent:")]
            headers.append(u"User-Agent: Mozilla/5.0 (Windows NT 10.0; rv:78.0) Gecko/20100101 Firefox/78.0")
            request = self._helpers.buildHttpMessage(headers, body)

            # Build HTTP service
            http_service = self._helpers.buildHttpService(host, port, protocol)

            # Make HTTP request
            request_response = self._callbacks.makeHttpRequest(http_service, request)

            if request_response is None:
                self.request_failed(url, "No response received")
                return

            raw_response = request_response.getResponse()
            if raw_response is None:
                self.request_failed(url, "Empty response")
                return

            # Analyze response
            response_info = self._helpers.analyzeResponse(raw_response)
            status_code = response_info.getStatusCode()
            response_length = len(raw_response)
            
            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000

            # Add successful result
            self.add_result(url, status_code, response_length, duration_ms, request_response)

        except Exception as e:
            # Categorize different types of errors
            error_str = unicode(e)
            if "UnknownHostException" in error_str:
                error_msg = "Host not found"
            elif "ConnectException" in error_str:
                error_msg = "Connection refused"
            elif "SocketTimeoutException" in error_str:
                error_msg = "Request timeout"
            elif "SSLException" in error_str or "SSL" in error_str:
                error_msg = "SSL/TLS error"
            elif "MalformedURLException" in error_str:
                error_msg = "Malformed URL"
            else:
                error_msg = "Network error: {}".format(error_str[:50])
            
            self._callbacks.printError(u"HTTP request failed for {}: {}".format(url, error_str))
            self.request_failed(url, error_msg)

    def paste_urls(self, event):
        clipboard = self.panel.getToolkit().getSystemClipboard()
        try:
            data = clipboard.getData(DataFlavor.stringFlavor)
            if data:
                self.text_area.append(data + '\n')
        except Exception as e:
            self._callbacks.printError(u"Error pasting from clipboard: {}".format(unicode(e)))

    def extensionUnloaded(self):
        """Clean up when extension is unloaded"""
        try:
            self.executor.shutdown()
            if not self.executor.awaitTermination(5, TimeUnit.SECONDS):
                self._callbacks.printError(u"Some tasks may not have completed correctly.")
        except Exception as e:
            self._callbacks.printError(u"Error terminating executor: {}".format(unicode(e)))


class RequestRunnable(Runnable):
    def __init__(self, parent, url):
        self.parent = parent
        self.url = url

    def run(self):
        try:
            self.parent.fetch_url(self.url)
        except Exception as e:
            self.parent.request_failed(self.url, "Thread exception: {}".format(unicode(e)))


class TableMouseListener(MouseAdapter):
    def __init__(self, parent):
        self.parent = parent
    
    def mouseClicked(self, event):
        if event.getClickCount() == 1:  # Single click
            selected_row = self.parent.results_table.getSelectedRow()
            if selected_row != -1:
                # Convert to model row index in case table is sorted
                model_row = self.parent.results_table.convertRowIndexToModel(selected_row)
                self.parent.on_table_selection(model_row)


class MessageEditorController(IMessageEditorController):
    def __init__(self, parent):
        self._parent = parent
    
    def getHttpService(self):
        return None
    
    def getRequest(self):
        return None
    
    def getResponse(self):
        return None
