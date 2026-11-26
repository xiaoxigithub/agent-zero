# Project Browser proxy

- Project Browser reaches this relay over loopback. Project Terminal uses a private Unix listener with the same validated public-address selection.

- Proxies HTTP and HTTPS connections for the isolated viewer on a loopback port.
- Resolves a public host before opening the socket and uses the same validated address for the connection. Private, loopback, link-local, multicast and reserved addresses are rejected.
- With the optional host proxy, an HTTPS DNS lookup can replace synthetic benchmarking addresses for hostnames. The host proxy receives only the validated numeric public destination.
- The viewer's kernel policy permits TCP connection to the proxy port and denies raw and datagram Internet sockets.
