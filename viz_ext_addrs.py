import subprocess
import simplekml
import threading
import requests
import click
import json
import time
import re
import os


isActive = True
default_loc = ('40.000', '-105.000')
user_lat = None
user_lon = None


def get_ext_addrs():
    """Gets current external network connections / IPs from netstat.
    Returns dict with valid cleaned IPs and ports."""

    curr_ext_addrs = {}
    sockets = []
    bad_addrs = ["127", "::1", "10"]

    conns = subprocess.run("netstat -n", stdout=subprocess.PIPE).stdout
    conns = conns.decode("utf-8")
    raw_conns = conns.split("\r\n")

    # Start slicing from index 4 after header information
    for conn in raw_conns[4:]:
        conn = conn.split()
        sockets.append(conn) if len(conn) > 0 else None

    for socket in sockets:
        socket = (
            re.split(":", socket[2])
            if "[" not in str(socket[2])
            else re.split("]:", socket[2])
        )

        addr = (socket[0].strip() if "[" not in socket[0]
                else socket[0].strip()[1:])
        port = socket[1].strip()
        new_socket = f"{addr}|{port}"

        if (new_socket not in curr_ext_addrs.keys()) and (
                not new_socket.startswith(tuple(bad_addrs))):
            curr_ext_addrs[f"{addr}|{port}"] = {"addr": addr, "port": port}

    return curr_ext_addrs


def get_whois_info(ext_addrs):
    """Get batch Whois information for valid external IPs.
    Returns resonse.json() or connection error message."""

    queries = []
    # Endpoint supports 15 requests / min (max 100 IPs per request)
    batch_endpoint = 'http://ip-api.com/batch'

    for addr in ext_addrs:
        queries.append({'query': ext_addrs[addr]['addr']})

    # If more than 100 IPs, take slice of first hundred only
    queries = queries if len(queries) <= 100 else queries[0:100]

    try:
        response = requests.post(batch_endpoint, data=json.dumps(queries))
    except requests.exceptions.ConnectionError as e:
        response = 'Connection error'

    return response


def make_addrs_kml(response):
    """Makes KML for IPs with lat-lons from Whois information.
    Writes KML to current working directory."""

    # Label, icon, and linestring styles are ABGR
    kml = simplekml.Kml()

    # User-defined location
    loc = kml.newpoint(name='origin', coords=[(user_lon, user_lat)])
    loc.style.labelstyle.color = '#ffffffff'
    loc.style.labelstyle.scale = 3
    loc.style.iconstyle.icon.href = 'placemark_circle.png'
    loc.style.iconstyle.color = '#ffffffff'
    loc.style.iconstyle.scale = 2

    for item in response:
        if item['status'] == 'success':
            ip = item['query']
            lat = item['lat']
            lon = item['lon']
            isp = item['isp']
            org = item['org']

            detail = (f'IP: {ip}\nISP: {isp}\nOrg: {org}\n'
                      f'Lat: {lat}\nLon: {lon}')

            label_1 = f'{org} ({ip})'
            label_2 = ip

            # Create ext addr points
            pnt = kml.newpoint(name=label_1 if org else label_2,
                               description=detail, coords=[(lon, lat)])
            pnt.style.labelstyle.color = simplekml.Color.white
            pnt.style.labelstyle.scale = 2
            pnt.style.iconstyle.icon.href = 'placemark_circle.png'
            pnt.style.iconstyle.color = ('#ff66ff00' if item['countryCode']
                                         == 'US' else '#ff0000ff')
            pnt.style.iconstyle.scale = 2

            # Create linestrings to ext addr points
            ls = kml.newlinestring(coords=[(user_lon, user_lat),
                                           (lon, lat)])
            ls.style.linestyle.width = 2
            ls.style.linestyle.color = ('#ff66ff00' if item['countryCode']
                                        == 'US' else '#ff0000ff')

            # Tessellate makes linestrings follow earth's curvature
            ls.tessellate = 1

        else:
            continue

    kml.save('viz_ext_addrs.kml')
    return 'finished'


def overwrite_kml():
    """Make simple KML with just user location as only point."""

    kml = simplekml.Kml()
    pnt = kml.newpoint(name="origin", coords=[(user_lon, user_lat)])
    pnt.style.labelstyle.color = '#ffffffff'
    pnt.style.labelstyle.scale = 3
    pnt.style.iconstyle.icon.href = 'placemark_circle.png'
    pnt.style.iconstyle.scale = 2
    pnt.style.iconstyle.color = '#ffffffff'
    kml.save("viz_ext_addrs.kml")


def get_quit_input():
    """Second thread that runs simultaneously to do_all function thread.
    Allows user to quit and stop the script at any time with 'q'."""

    global isActive
    user_input = input().lower().strip()

    if user_input == 'q':
        isActive = False
        overwrite_kml()
        os._exit(1)
    else:
        input_thread = threading.Thread(target=get_quit_input)
        input_thread.start()


def do_all():
    """Iterates getting external IP addresses, sending POST requests
    to batch API endpoint, and creating new KML every 5 seconds."""

    global isActive
    local_time = time.strftime('%m/%d/%Y, %H:%M:%S', time.localtime())
    print('Started at: ', local_time)
    print('Enter "q" to quit')

    while isActive:
        curr_ext_addrs = get_ext_addrs()
        response = get_whois_info(curr_ext_addrs)

        # Back off for 1 minute if requests returns server connection error
        if response != 'Connection error':
            # print('response.headers: ', response.headers)
            rate_limit = int(response.headers['X-Rl'])
            # print('rate limit: ', rate_limit)
            back_off = int(response.headers['X-Ttl'])
            make_addrs_kml(response.json())

            # Add back off time to next iteration if rate limit exceeded
            if rate_limit > 0:
                time.sleep(5.0)
            else:
                print('Exceeded API rate limit on last iteration.'
                      'Backing off before next iteration.')
                time.sleep(5.0 + back_off)
                print('Resumed at: ', local_time)

        else:
            print('Server connection error. Backing off for 1 minute.')
            time.sleep(60.0)
            print('Resumed at: ', local_time)
            continue


@click.command()
@click.option('--loc', default=default_loc, nargs=2, type=str,
              help="User's location in decimal-degree latitude"
              "-longitude format. Defaults to '40.000, -105.000' "
              "(Broomfield, CO, USA) if no location is provided.")
def main(loc):
    """
    A tool to visualize near-real time device connections to external
    IP addresses for situational awareness. Leverages: 

    1) Subprocess (Python module) and netstat (Windows) to get current 
    device connections.

    2) Ip-api.com which offers a free, no sign-up endpoint for querying 
    Whois information in bulk.

    3) Google Earth which allows adding a network link layer with auto-
    refresh options for the linked KML.
    """
    global user_lat, user_lon
    user_lat = loc[0]
    user_lon = loc[1]

    input_thread = threading.Thread(target=get_quit_input)
    input_thread.start()
    do_all_thread = threading.Thread(target=do_all)
    do_all_thread.start()


if __name__ == "__main__":
    main()
