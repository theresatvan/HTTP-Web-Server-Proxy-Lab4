from socket import *
import sys
import os
import shutil
import select

### For sake of readablity, added code will be documented with comments tagged by 3 #'s

cacheDir = os.path.join(os.path.dirname(__file__), 'cache')

# For WINDOWS: can't keyboard interrupt while the program is in a blocking call
# Workaround is to timeout a blocking call every timeLeft seconds so the program can
# respond to any SIGINT or SIGKILL signals
# Shouldn't be a problem on Mac or Linux
# Additional note for WINDOWS: select.select() only works on sockets, so waitable should be a socket
def wait_interruptible(waitable, timeLeft):
    while True:
        ready = select.select([waitable], [], [], timeLeft)
        if len(ready[0]) > 0:
            return

# interruptible versions of accept(), recv(), readline(), read()
def interruptible_accept(socket):
    wait_interruptible(socket, 5)
    return socket.accept()

def interruptible_recv(socket, nbytes):
    wait_interruptible(socket, 5)
    return socket.recv(nbytes)

def interruptible_readline(fileObj):
    wait_interruptible(fileObj, 5)
    return fileObj.readline()

def interruptible_read(fileObj, nbytes=-1):
    wait_interruptible(fileObj, 5)
    return fileObj.read(nbytes)

# Read an HTTP message from a socket file object and parse it
# sockf: Socket file object to read from
### Returns: (headline: str, [(header: str, header_value: str), content: str])
def parse_http_headers(sockf):
    # Read the first line from the HTTP message
    # This will either be the Request Line (request) or the Status Line (response)
    headline = interruptible_readline(sockf).decode().strip()

    # Set up list for headers
    headers = []
    while True:
        # Read a line at a time
        header = interruptible_readline(sockf).decode()
        
        # If it's the empty line '\r\n', it's the end of the header section
        if len(header.rstrip('\r\n')) == 0:
            break

        # Partition header by colon
        headerPartitions = header.partition(':')

        # Skip if there's no colon
        if headerPartitions[1] == '':
            continue

        headers.append((headerPartitions[0].strip(), headerPartitions[2].strip()))
        
    ### Read contents if Content-Length header exists
    content = ""
    
    if any(['Content-Length' in header for header in headers]):
        contentLength = [val for header, val in headers if header == 'Content-Length']
        content += interruptible_read(sockf, int(contentLength[0])).decode()

    ### Return a 3-tuple: content along with headline and headers
    return(headline, headers, content)

# Forward a server response to the client and save to cache
# sockf: Socket file object connected to server
# fileCachePath: Path to cache file
# clisockf: Socket file object connected to client
### requestMethod: Request method of original client request
def forward_and_cache_response(sockf, fileCachePath, clisockf, requestMethod):
    cachef = None

    # Create the intermediate directories to the cache file
    ### Only create a cache file if client request was HTTP GET
    if fileCachePath is not None and requestMethod == "GET":
        os.makedirs(os.path.dirname(fileCachePath), exist_ok=True)
        # Open/create cache file
        cachef = open(fileCachePath, 'w+b')

    try:
        # Read response from server
        ### Read content from server response as well
        statusLine, headers, content = parse_http_headers(sockf)
        # Filter out the Connection header from the server
        headers = [h for h in headers if h[0] != 'Connection']
        # Replace with our own Connection header
        # We will close all connections after sending the response.
        # This is an inefficient,  single-threaded proxy!
        headers.append(('Connection', 'close'))
        
        # Fill in start.
        ### Initialize data buffer with response status line, each line ends with '\r\n'
        data = statusLine + '\r\n'

        ### Add rest of response headers and their corresponding values to the data buffer
        for header in headers:
            data += header[0] + ": " + header[1] + '\r\n'
        
        ### Concatenate '\r\n' to data buffer to denote end of header section and content from the server response
        data += '\r\n' + content
        
        ### Only write to cache file if original client request is HTTP GET
        if requestMethod == 'GET': cachef.write(data.encode())
        
        ### Encode and forward response to client socket file object
        clisockf.write(data.encode())
        # Fill in end.
        
    except Exception as e:
        print(e)
    finally:
        if cachef is not None:
            cachef.close()

# Forward a client request to a server
# sockf: Socket file object connected to server
# requestUri: The request URI to request from the server
# hostn: The Host header value to include in the forwarded request
# origRequestLine: The Request Line from the original client request
# origHeaders: The HTTP headers from the original client request
### origContent: The Contents from the original client request
def forward_request(sockf, requestUri, hostn, origRequestLine, origHeaders, origContent):
    # Filter out the original Host header and replace it with our own
    headers = [h for h in origHeaders if h[0] != 'Host']
    headers.append(('Host', hostn))
    # Send request to the server
    
    # Fill in start.
    ### Initialize data buffer with headline, each line ends with '\r\n'
    data = origRequestLine + '\r\n'
    
    ### Add rest of headers and their corresponding values to the data buffer
    for header in headers:
        data += header[0] + ": " + header[1] + '\r\n'
    
    ### Concatenate '\r\n' to data buffer to denote end of header section and content from the original client request
    data += '\r\n' + origContent

    ### Encode and write data to socket file object connected to server
    sockf.write(data.encode())
    # Fill in end.

def proxyServer(port):
    if os.path.isdir(cacheDir):
        shutil.rmtree(cacheDir)
    # Create a server socket, bind it to a port and start listening
    tcpSerSock = socket(AF_INET, SOCK_STREAM)

    # Fill in start.
    ### Bind socket to specified port and listen for incoming TCP packets
    tcpSerSock.bind(('', port))
    tcpSerSock.listen()
    # Fill in end.

    tcpCliSock = None
    try:
        while 1:
            # Start receiving data from the client
            print('Ready to serve...')
            tcpCliSock, addr = interruptible_accept(tcpSerSock)

            print('Received a connection from:', addr)
            cliSock_f = tcpCliSock.makefile('rwb', 0)

            # Read and parse request from client
            ### 3-tuple is returned: client request headline, client request headers, and client request content
            requestLine, requestHeaders, requestContent = parse_http_headers(cliSock_f)

            if len(requestLine) == 0:
                continue

            # Extract the request URI from the given message
            requestUri = requestLine.split()[1]
            
            ### Extract request method from request line
            requestMethod = requestLine.split()[0]
            
            # if a scheme is included, split off the scheme, otherwise split off a leading slash
            uri_parts = requestUri.partition('http://')
            if uri_parts[1] == '':
                filename = requestUri.partition('/')[2]
            else:
                filename = uri_parts[2]

            print(f'filename: {filename}')

            if len(filename) > 0:
                # Compute the path to the cache file from the request URI
                # Change for Part Three
                ### Define cached file path from requested file in original client request
                fileCachePath = 'cacheDir/' + filename.partition('/')[2]
                ### Check if cached file path exists
                cached = os.path.exists(fileCachePath)
                
                print(f'fileCachePath: {fileCachePath}')

                # Check whether the file exists in the cache
                if fileCachePath is not None and cached:
                    # Read response from cache and transmit to client
                    # Fill in start.
                    ### Open and read contents from cached file into buffer
                    cacheFile = open(fileCachePath, 'rb')
                    cacheContent = cacheFile.read()

                    ### Write contents from buffer into client socket file object
                    cliSock_f.write(cacheContent)
                    # Fill in end.
                    print('Read from cache')
                else:
                    # Create a socket on the ProxyServer
                    # Fill in start.
                    ### Create socket             
                    c = socket(AF_INET, SOCK_STREAM)
                    # Fill in end.
                    hostn = filename.partition('/')[0]
                    print(f'hostn: {hostn}')

                    try:
                        # Connect to the socket
                        # Fill in start.
                        ### Separate server address and server port number from hostn
                        serverAddress = hostn.partition(':')
                        
                        ### Connect to server socket
                        c.connect((serverAddress[0], int(serverAddress[2])))
                        # Fill in end.

                        # Create a temporary file on this socket and ask port 80 for the file requested by the client
                        fileobj = c.makefile('rwb', 0)
                        ### Pass client request content into parameters as well
                        forward_request(fileobj, f'/{filename.partition("/")[2]}', hostn, requestLine, requestHeaders, requestContent)

                        # Read the response from the server, cache, and forward it to client
                        ### Pass client request method into parameters as well
                        forward_and_cache_response(fileobj, fileCachePath, cliSock_f, requestMethod)
                    except Exception as e:
                        print(e)
                    finally:
                        c.close()
            tcpCliSock.close()
    except KeyboardInterrupt:
        pass

    # Close the server socket and client socket
    # Fill in start.
    ### Close server socket and client socket
    tcpSerSock.close()
    tcpCliSock.close()
    # Fill in end.
    sys.exit()

if __name__ == "__main__":
    proxyServer(8888)
