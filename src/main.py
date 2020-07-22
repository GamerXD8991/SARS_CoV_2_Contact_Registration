# -*- coding: utf-8 -*-
'''
Created on 06.06.2020

@author: Input
'''

import flask
import flask_login
import os
import sys
import logging
import json
import ilsc

from twisted.internet import reactor, ssl
from twisted.web.server import Site
from twisted.web.wsgi import WSGIResource
from ilsc import caesar
from apscheduler.schedulers.background import BackgroundScheduler

try:
    import urlparse
except ImportError:
    import urllib.parse

##
# Debug
##

#try: import pydevd;pydevd.settrace('127.0.0.7',stdoutToServer=True, stderrToServer=True) #@UnresolvedImport
#except ImportError: print('somethin is wrong') #None # avoids throwing an Exception when not in debug mode

##
# Global objects
##

appCommandLine = None
appConfig = None
flaskApp = None
flaskLoginManager = None
appBackend = None

##
# Globals constants
##

DEFAULT_HTTP_PORT = 8080
DEFAULT_WEBSOCKET_PORT = 8888

# magic user id of the admin
MAGIC_USER_ID = 1

##
# Config
##

appCommandLine = ilsc.CommandLine.parse_args()

appConfig = ilsc.Config()

if appCommandLine.config:
    try:
        appConfig.read(appCommandLine.config)
    except Exception as e:
        print('%s: %s' % (sys.argv[0], str(e)))
        sys.exit(1)

# Log level
if appCommandLine.log_level:
    appConfig.log['level'] = appCommandLine.log_level
# Log file
if appCommandLine.log_file:
    appConfig.log['file'] = appCommandLine.log_file

##
# Logging
##
filename=appConfig.log['file']
logging.basicConfig(format='[%(levelname)s] %(asctime)s %(module)s::%(funcName)s: %(message)s',
                    filename=appConfig.log['file'],
                    level=logging.getLevelName(appConfig.log['level']))

##
# Configuration sanity checks
# Command line options have higher priority
##
# App
if appCommandLine.static_folder:
    appConfig.app['static_folder'] = appCommandLine.static_folder
if not appConfig.app['static_folder']:
    appConfig.app['static_folder'] = 'static'
if appCommandLine.template_folder:
    appConfig.app['template_folder'] = appCommandLine.template_folder
if not appConfig.app['template_folder']:
    appConfig.app['template_folder'] = 'templates'
# Http
if appCommandLine.port:
    appConfig.http['port'] = appCommandLine.port
else:
    try:
        appConfig.http['port'] = int(appConfig.http['port'])
    except Exception as e:
        logging.warning('failed to cast http port value to int: %s' % str(e))
        logging.warning('using default http port value: %d' % (DEFAULT_HTTP_PORT))
        appConfig.http['port'] = DEFAULT_HTTP_PORT
if appCommandLine.address:
    appConfig.http['address'] = appCommandLine.address
# Websocket
if appCommandLine.websocket_port:
    appConfig.websocket['port'] = appCommandLine.websocket_port
else:
    try:
        appConfig.websocket['port'] = int(appConfig.websocket['port'])
    except Exception as e:
        logging.warning('failed to cast websocket port value to int: %s' % str(e))
        logging.warning('using default websocket port value: %d' % (DEFAULT_WEBSOCKET_PORT))
        appConfig.websocket['port'] = DEFAULT_WEBSOCKET_PORT
if appCommandLine.websocket_address:
    appConfig.websocket['address'] = appCommandLine.websocket_address
# Database path
if appCommandLine.database:
    appConfig.database['path'] = appCommandLine.database

##
# Utils
##

# Expands given path to the absolute path
def __abspath(path):
    path = os.path.expanduser(path)
    path = os.path.expandvars(path)
    path = os.path.abspath(path)
    return path

def __render(template, **kwargs):
    try:
        try:
            hostname = urlparse.urlparse(flask.request.url).hostname
        except:
            hostname = urllib.parse.urlparse(flask.request.url).hostname
        if not appConfig.http['address']:
            appConfig.http['address'] = hostname
        if not appConfig.websocket['address']:
            appConfig.websocket['address'] = hostname
    except Exception as e:
        logging.warning(str(e))
    # pass always config data
    try: 
        return flask.render_template(template, config=appConfig.dict(), is_admin = is_admin(), **kwargs)
    except Exception as e:
        logging.error(str(e))

def check_messages():
    '''
    check if message should be displayed
    '''
    if 'messages' in flask.session.keys():
        msg = flask.session['messages']
        flask.session.pop('messages')
        return json.loads(msg)
    return None

def is_admin():
    '''
    return True if user is no admin
    '''
    if '_user_id' in flask.session.keys():
        return flask.session['_user_id'] and appBackend.check_admin(flask.session['_user_id'])
    else:
        return False

def get_user_devision():
    '''
    return current user devision
    '''
    if flask.session['_user_id']:
        return appBackend.get_user_devision(flask.session['_user_id'])
    return None

##
# Initialization
##

flaskApp = flask.Flask(__name__,
                   static_folder=appConfig.app['static_folder'],
                   template_folder=appConfig.app['template_folder'])
if appCommandLine.flask_config:
    appCommandLine.flask_config = __abspath(appCommandLine.flask_config)
    flaskApp.config.from_pyfile(appCommandLine.flask_config)

# database from the command line overrides one from the Flask configuration
if appConfig.database['path']:
    database_schema = 'sqlite:///'
    if appConfig.database['path'].startswith(database_schema):
        database_uri = appConfig.database['path']
    else:
        database_uri = database_schema + format(__abspath(appConfig.database['path']))
    flaskApp.config.update(SQLALCHEMY_DATABASE_URI=database_uri)
# database uri should not be empty
if 'SQLALCHEMY_DATABASE_URI' not in flaskApp.config or not flaskApp.config['SQLALCHEMY_DATABASE_URI']:
    logging.error('Invalid SQLALCHEMY_DATABASE_URI: empty')
    sys.exit(1)
# database uri should not be set to memory
if ':memory:' in flaskApp.config['SQLALCHEMY_DATABASE_URI']:
    logging.error('Invalid SQLALCHEMY_DATABASE_URI: :memory:')
    sys.exit(1)
# set random secret key if not provided in the Flask configuration
if 'SECRET_KEY' not in flaskApp.config or not flaskApp.config['SECRET_KEY']:
    flaskApp.config.update(SECRET_KEY=os.urandom(24))

# check and create private public keypair if not existing
if not caesar.check_keys_exsist(appConfig.database['privkey'], appConfig.database['pubkey']):
    caesar.generate_keys(appConfig.database['privkey'], appConfig.database['pubkey'])

# finish database initialization
ilsc.appDatabase.init_app(flaskApp)

# init login manager
flaskLoginManager = flask_login.LoginManager(flaskApp)

@flaskLoginManager.user_loader
def user_loader(userid):
    return ilsc.User.query.filter_by(userid=userid).first()

@flaskLoginManager.unauthorized_handler
def unauthorized():
    return flask.redirect(flask.url_for('r_signin'),code=302)

# init backend
appBackend = ilsc.Backend(appConfig, flaskApp, ilsc.appDatabase, MAGIC_USER_ID)

##
# Routing
##

@flaskApp.route('/', methods=['GET', 'POST'])
def r_regform():
    form = ilsc.forms.RegisterForm()
    msg = None
    if form.validate_on_submit():
        guid = appBackend.gen_guid()
        success, msg = appBackend.enter_guest(form, guid)
        if success:
            appBackend.make_qr(guid, scale=20)
            return flask.redirect(flask.url_for('r_getqr', guid=guid, n=1),code=302)
            
    return __render('mainform.html', form = form, msg = msg)

@flaskApp.route('/qr/<guid>')
def r_getqr(guid):
    if appBackend.check_guid(guid):
        info = False
        if 'n' in flask.request.args and flask.request.args['n'] == '1':
            info = True
        return __render('qrcode.html', info=info, guid=guid,host=appConfig.http['address'], port=appConfig.http['port'])
    return flask.redirect(flask.url_for('r_regform'),code=302)
    #flask.abort(404)

@flaskApp.route('/impressum')
def r_imprint():
    return __render(appConfig.app['imprint'])

@flaskApp.route('/datenschutz')
def r_gprd():
    return __render(appConfig.app['gprd'])

@flaskApp.route('/scan', methods=['GET', 'POST'])
@flask_login.login_required
def r_scanning():
    try:
        devision_id = get_user_devision()
        devision = appConfig.app['devisions'][devision_id]
        count = appBackend.count_guests(devision_id)
        return __render('scanning.html', wsocket=appConfig.websocket, count = count, devision_id = devision_id, devision = devision)
    except Exception as e:
        print(e)

@flaskApp.route('/signin', methods=['GET', 'POST'])
def r_signin():
    if 'GET' == flask.request.method:
        return __render('signin.html')
    # POST
    errors = []
    # obtain form data
    username = flask.request.form['username'] if 'username' in flask.request.form else ''
    password = flask.request.form['password'] if 'password' in flask.request.form else ''
    # validate user
    try:
        user = ilsc.User.query.filter_by(username=username).first()
        if user and user.validate_password(password):
            if user.active:
                flask_login.login_user(user)
                return flask.redirect(flask.url_for('r_scanning'),code=302)
            else:
                errors.append(f'Fehler: Nutzer "{username}" inaktiv.')
        else:
            errors.append(f'Fehler: Passwort oder Nutzername "{username}" ist falsch.')
        logging.warning(str(errors))
        return __render('signin.html', errors=errors)
    except Exception as e:
        logging.warning(str(e))

@flaskApp.route('/signout', methods=['GET'])
@flask_login.login_required
def r_signout():
    flask_login.logout_user()
    return flask.redirect(flask.url_for('r_signin'),code=302)

@flaskApp.route('/users')#, methods=['GET','POST'])
@flask_login.login_required
def r_users():
    if not is_admin():
        return __render('nope.html')

    devisions = appConfig.app['devisions']
    users = ilsc.User.query.all()
    msg = check_messages()
    return __render('users.html', users=users, msg=msg, dev = devisions)

@flaskApp.route('/user/edit/<uid>', methods=['GET', 'POST'])
@flask_login.login_required
def r_user_edit(uid):
    '''
    render user edit form
    '''
    if not is_admin():
        return __render('nope.html')

    devisions = []
    for i in range(len(appConfig.app['devisions'])):
        devisions.append((str(i),appConfig.app['devisions'][i]))
    form = ilsc.forms.UserForm(choices = devisions)
    user = appBackend.fetch_user(uid)

    if user:
        if 'POST' == flask.request.method and form.validate_on_submit():
            success, msg = appBackend.update_user(user.id, form)
            if success:
                flask.session['messages'] = json.dumps({"success": msg})
            else:
                flask.session['messages'] = json.dumps({"error": msg})
            return flask.redirect(flask.url_for('r_users'),code=302)
        
        form = ilsc.forms.UserForm(obj = user, choices = devisions)
        return __render('user_edit.html', form = form, uid=uid, superuser = MAGIC_USER_ID==user.id)

    else:
        return flask.redirect(flask.url_for('r_users'),code=302)

@flaskApp.route('/user/password/<uid>', methods=['GET', 'POST'])
@flask_login.login_required
def r_user_passwd(uid):
    '''
    render user edit form
    '''
    if not is_admin():
        return __render('nope.html')

    form = ilsc.forms.ChangePasswd()
    user = appBackend.fetch_user(uid)
    if user:
        if 'POST' == flask.request.method and form.validate_on_submit():
            success, msg = appBackend.update_password(user.id, form)
            if success:
                flask.session['messages'] = json.dumps({"success": msg})
            else:
                flask.session['messages'] = json.dumps({"error": msg})
            return flask.redirect(flask.url_for('r_users'),code=302)
        
        form = ilsc.forms.ChangePasswd(obj = user)
        return __render('user_password.html', form = form, uid=uid, superuser = MAGIC_USER_ID==user.id)
    else:
        return flask.redirect(flask.url_for('r_users'),code=302)

@flaskApp.route('/user/delete/<uid>', methods=['GET', 'POST'])
@flask_login.login_required
def r_user_delete(uid):
    '''
    render user delete form
    '''
    if not is_admin():
        return __render('nope.html')

    form = None
    return __render('user_delete.html', form = form, uid=uid)

@flaskApp.route('/user/add', methods=['GET', 'POST'])
@flask_login.login_required
def r_user_add():
    '''
    render user delete form
    '''
    if not is_admin():
        return __render('nope.html')

    devisions = []
    for i in range(len(appConfig.app['devisions'])):
        devisions.append((str(i),appConfig.app['devisions'][i]))
    form = ilsc.forms.UserAddForm(choices = devisions)
    devisions = []
    if 'POST' == flask.request.method and form.validate_on_submit():
        #TODO: give feedback
        appBackend.add_user(username=form.username.data,
                        password=form.password.data,
                        devision = form.devision.data,
                        isadmin = form.isadmin.data,
                        do_hash=True)
        return flask.redirect(flask.url_for('r_users'),code=302)
    else:
        return __render('user_add.html', form = form, choices = devisions)
    return __render('user_add.html', form = form, choices = devisions)

@flaskApp.route('/user-remove', methods=['POST'])
@flask_login.login_required
def r_user_remove():
    '''
    remove user
    '''
    if not is_admin():
        return __render('nope.html')
    try:
        _userid = flask.request.form['userid'] if 'userid' in flask.request.form else 0
        if MAGIC_USER_ID == int(_userid): raise ValueError('user id=%s is protected' % str(_userid))
        if appBackend.delete_user(_userid):
            return flask.redirect(flask.url_for('r_users'),code=302)
        return flask.redirect(flask.url_for('r_users'),code=302)
    except Exception as e:
        logging.warning(str(e))
    return ('', 204)

@flaskApp.route('/guests', methods=['GET','POST'])
@flask_login.login_required
def r_guests():
    '''
    display all guest at given day
    '''
    if not is_admin():
        return __render('nope.html')
    guests = []
    devisions = appConfig.app['devisions']
    form = ilsc.forms.DateForm()
    if form.validate_on_submit():
        date = form.visitdate.data#.strftime('%Y-%m-%d')
        guests = appBackend.fetch_guests(date)
    return __render('guests.html', form=form, guests=guests, devisions = devisions)

@flaskApp.route('/visits/<guid>', methods=['GET'])
@flask_login.login_required
def r_visits(guid):
    '''
    display all visits by guid
    '''
    if not is_admin():
        return __render('nope.html')
    visits = []
    devisions = appConfig.app['devisions']
    form = ilsc.forms.DateForm()
    if appBackend.check_guid(guid):
        visits = appBackend.fetch_visits(guid)
    return __render('visits.html', form=form, visits=visits, devisions=devisions)

@flaskApp.errorhandler(404)
def r_page_not_found(error):
    '''
    404 Page
    '''
    return flask.render_template('404.html', title = '404'), 404
##
# Main entry point
##
logging.debug('%r' % appConfig)
with flaskApp.app_context():
    if not ilsc.check_database(flaskApp.config['SQLALCHEMY_DATABASE_URI']):
        # initialize database
        ilsc.init_database()

def cleanup_everything():
    '''
    check and clean database
    '''
    appBackend.checkout_all()
    appBackend.clean_obsolete_checkins()
    appBackend.clean_obsolete_contacts()

#Background scheduler
scheduler = BackgroundScheduler({'apscheduler.timezone': appConfig.app['timezone']}) 
for h in appConfig.app['autocheckout']:
    scheduler.add_job(cleanup_everything, 'cron', hour=h, minute=0)
scheduler.start()

if appConfig.app['cleanonstart']:
    cleanup_everything()


#appBackend.inject_random_userdata()#just for testing
try:
    if appConfig.http['usessl']:
        priv = appConfig.http['serverpriv']
        cacert = appConfig.http['servercacert']
        certdata = ssl.DefaultOpenSSLContextFactory(priv, cacert)
        reactor.listenSSL(appConfig.http['port'], Site(WSGIResource(reactor, reactor.getThreadPool(), flaskApp)),certdata)
        reactor.listenSSL(appConfig.websocket['port'], appBackend.get_websocket_site(),certdata)
    else:
        reactor.listenTCP(appConfig.http['port'], Site(WSGIResource(reactor, reactor.getThreadPool(), flaskApp)))
        reactor.listenTCP(appConfig.websocket['port'], appBackend.get_websocket_site())
except Exception as e:
    logging.warning(str(e))
    print(e)
    
if __name__ == "__main__":
    try:
        reactor.run()
    except Exception as e:
        logging.warning(str(e))
        print(e)