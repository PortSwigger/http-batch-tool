# -*- coding: utf-8 -*-
from burp import IBurpExtender, ITab, IExtensionStateListener, IHttpRequestResponse
from java.awt import BorderLayout, Color, Font, Dimension
from java.awt.datatransfer import DataFlavor
from javax.swing import JPanel, JTextArea, JButton, JScrollPane, JPopupMenu, JMenuItem, JTable, JSplitPane, JLabel, SwingConstants
from javax.swing.table import DefaultTableModel
from java.net import URL, MalformedURLException
from java.util.concurrent import Executors, TimeUnit
from java.lang import Runnable
from threading import Lock
import time

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

        # Register listener
        self._callbacks.registerExtensionStateListener(self)

        # UI Setup
        self.setup_ui()
        self._callbacks.addSuiteTab(self)

    def setup_ui(self):
        # Main panel
        self.panel = JPanel(BorderLayout())
        
        # Create split pane for input and results
        split_pane = JSplitPane(JSplitPane.VERTICAL_SPLIT)
        
        # Top panel for URL input
        top_panel = JPanel(BorderLayout())
        
        # URL input area
        self.text_area = JTextArea(10, 50)
        self.text_area.setFont(Font("Monospaced", Font.PLAIN, 12))
        
        # Popup menu for paste functionality
        popup_menu = JPopupMenu()
        paste_item = JMenuItem("Paste URLs", actionPerformed=self.paste_urls)
        popup_menu.add(paste_item)
        self.text_area.setComponentPopupMenu(popup_menu)
        
        top_panel.add(JScrollPane(self.text_area), BorderLayout.CENTER)
        
        # Button panel
        button_panel = JPanel()
        self.send_button = JButton("Send Requests", actionPerformed=self.on_send_requests)
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
        bottom_panel = JPanel(BorderLayout())
        
        # Results table
        self.table_model = DefaultTableModel()
        self.table_model.addColumn("URL")
        self.table_model.addColumn("Status")
        self.table_model.addColumn("Length")
        self.table_model.addColumn("Time")
        
        self.results_table = JTable(self.table_model)
        self.results_table.setAutoCreateRowSorter(True)
        self.results_table.getColumnModel().getColumn(0).setPreferredWidth(400)
        self.results_table.getColumnModel().getColumn(1).setPreferredWidth(80)
        self.results_table.getColumnModel().getColumn(2).setPreferredWidth(80)
        self.results_table.getColumnModel().getColumn(3).setPreferredWidth(100)
        
        results_scroll = JScrollPane(self.results_table)
        results_scroll.setPreferredSize(Dimension(600, 300))
        
        bottom_panel.add(JLabel("Request Results:"), BorderLayout.NORTH)
        bottom_panel.add(results_scroll, BorderLayout.CENTER)
        
        # Configure split pane
        split_pane.setTopComponent(top_panel)
        split_pane.setBottomComponent(bottom_panel)
        split_pane.setDividerLocation(200)
        split_pane.setResizeWeight(0.4)
        
        self.panel.add(split_pane, BorderLayout.CENTER)

    def getTabCaption(self):
        return u"HTTP Batch Tool"

    def getUiComponent(self):
        return self.panel

    def on_send_requests(self, event):
        raw_text = self.text_area.getText()
        if not raw_text.strip():
            self._callbacks.printOutput(u"No URLs provided.")
            self.update_status("No URLs provided")
            return

        urls = raw_text.strip().split('\n')
        valid_urls = []
        
        for url in urls:
            url = url.strip()
            if url and url.startswith("http"):
                valid_urls.append(url)
            elif url:
                self._callbacks.printError(u"Ignoring invalid URL: " + unicode(url))

        if not valid_urls:
            self.update_status("No valid URLs found")
            return

        # Reset counters
        self.request_count = len(valid_urls)
        self.completed_count = 0
        
        # Clear previous results
        self.table_model.setRowCount(0)
        
        self._callbacks.printOutput(u"Starting {} requests...".format(self.request_count))
        self.update_status("Sending {} requests...".format(self.request_count))
        
        # Disable button during processing
        self.send_button.setEnabled(False)
        
        for url in valid_urls:
            self.executor.submit(RequestRunnable(self, url))

    def update_status(self, message):
        """Update the status label safely from any thread"""
        try:
            self.status_label.setText(message)
        except:
            pass  # Ignore threading issues with UI updates

    def add_result(self, url, status_code, response_length, duration_ms):
        """Add a result to the table (thread-safe)"""
        try:
            # Format duration
            if duration_ms < 1000:
                time_str = "{}ms".format(int(duration_ms))
            else:
                time_str = "{:.1f}s".format(duration_ms / 1000.0)
            
            # Add row to table
            row = [url, str(status_code), str(response_length), time_str]
            self.table_model.addRow(row)
            
            # Update completion count
            with self.lock:
                self.completed_count += 1
                if self.completed_count >= self.request_count:
                    # All requests completed
                    self.update_status("Completed {} requests successfully".format(self.request_count))
                    self.send_button.setEnabled(True)
                    self._callbacks.printOutput(u"All {} requests completed. Results available in HTTP Batch Tool tab.".format(self.request_count))
                else:
                    self.update_status("Completed {}/{} requests".format(self.completed_count, self.request_count))
        except Exception as e:
            self._callbacks.printError(u"Error updating results: {}".format(unicode(e)))

    def request_failed(self, url, error_msg):
        """Handle failed requests"""
        try:
            row = [url, "ERROR", "0", error_msg]
            self.table_model.addRow(row)
            
            with self.lock:
                self.completed_count += 1
                if self.completed_count >= self.request_count:
                    self.update_status("Completed {} requests (some failed)".format(self.request_count))
                    self.send_button.setEnabled(True)
                    self._callbacks.printOutput(u"All {} requests completed. Results available in HTTP Batch Tool tab.".format(self.request_count))
                else:
                    self.update_status("Completed {}/{} requests".format(self.completed_count, self.request_count))
        except Exception as e:
            self._callbacks.printError(u"Error updating failed result: {}".format(unicode(e)))

    def fetch_url(self, url):
        start_time = time.time()
        
        try:
            with self.lock:
                self._callbacks.printOutput(u"==> Starting request for: {}".format(unicode(url)))

            try:
                parsed_url = URL(url)
            except MalformedURLException as e:
                error_msg = "Malformed URL: {}".format(unicode(e))
                with self.lock:
                    self._callbacks.printError(u"Malformed URL: {} -> {}".format(unicode(url), unicode(e)))
                self.request_failed(url, error_msg)
                return

            host = parsed_url.getHost()
            port = parsed_url.getPort()
            protocol = parsed_url.getProtocol()
            path = parsed_url.getFile()

            if port == -1:
                port = 443 if protocol == "https" else 80

            with self.lock:
                self._callbacks.printOutput(u">> Host: {}, Port: {}, Protocol: {}, Path: {}".format(
                    unicode(host), port, unicode(protocol), unicode(path)))

            request = self._helpers.buildHttpRequest(parsed_url)
            analyzed = self._helpers.analyzeRequest(request)
            headers = list(analyzed.getHeaders())
            body = request[analyzed.getBodyOffset():]

            headers = [h for h in headers if not h.lower().startswith("user-agent:")]
            headers.append(u"User-Agent: Mozilla/5.0 (Windows NT 10.0; rv:78.0) Gecko/20100101 Firefox/78.0")
            request = self._helpers.buildHttpMessage(headers, body)

            http_service = self._helpers.buildHttpService(host, port, protocol)

            with self.lock:
                self._callbacks.printOutput(u">> Sending request to: {}:{}{}".format(
                    unicode(host), port, unicode(path)))

            response = self._callbacks.makeHttpRequest(http_service, request)

            if response is None:
                error_msg = "No response received"
                with self.lock:
                    self._callbacks.printError(u"!!! makeHttpRequest returned None for: {}".format(unicode(url)))
                self.request_failed(url, error_msg)
                return

            raw_response = response.getResponse()
            if raw_response is None:
                error_msg = "Empty response"
                with self.lock:
                    self._callbacks.printError(u"!!! getResponse() returned None for: {}".format(unicode(url)))
                self.request_failed(url, error_msg)
                return

            response_info = self._helpers.analyzeResponse(raw_response)
            status_code = response_info.getStatusCode()
            response_length = len(raw_response)
            
            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000

            with self.lock:
                self._callbacks.printOutput(u"[{}] {} - {} bytes".format(status_code, unicode(url), response_length))

            # Add to results table
            self.add_result(url, status_code, response_length, duration_ms)

        except Exception as e:
            error_msg = "Exception: {}".format(unicode(e)[:50])  # Truncate long error messages
            with self.lock:
                try:
                    self._callbacks.printError(u"!!! Error processing '{}': {}".format(unicode(url), unicode(e)))
                except:
                    self._callbacks.printError(u"!!! Error processing URL (encoding failure)")
            self.request_failed(url, error_msg)

    def paste_urls(self, event):
        clipboard = self.panel.getToolkit().getSystemClipboard()
        try:
            data = clipboard.getData(DataFlavor.stringFlavor)
            if data:
                self.text_area.append(data + '\n')
                self._callbacks.printOutput(u"URLs pasted into text field.")
                self.update_status("URLs pasted - ready to send")
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
        self.parent.fetch_url(self.url)
