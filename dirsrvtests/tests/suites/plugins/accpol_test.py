# --- BEGIN COPYRIGHT BLOCK ---
# Copyright (C) 2017 Red Hat, Inc.
# All rights reserved.
#
# License: GPL (version 3 or any later version).
# See LICENSE for details.
# --- END COPYRIGHT BLOCK ---
#
import pytest
import subprocess
from lib389.tasks import *
from lib389.utils import *
from lib389.topologies import topology_st
from lib389.idm.user import UserAccounts

from lib389._constants import PLUGIN_ACCT_POLICY, DN_PLUGIN, DN_DM, PASSWORD, DEFAULT_SUFFIX, DN_CONFIG

LOCL_CONF = 'cn=AccountPolicy1,ou=people,dc=example,dc=com'
TEMPL_COS = 'cn=TempltCoS,ou=people,dc=example,dc=com'
DEFIN_COS = 'cn=DefnCoS,ou=people,dc=example,dc=com'
ACCPOL_DN = "cn={},{}".format(PLUGIN_ACCT_POLICY, DN_PLUGIN)
ACCP_CONF = "{},{}".format(DN_CONFIG, ACCPOL_DN)
USER_PASW = 'Secret1234'
INVL_PASW = 'Invalid234'


@pytest.fixture(scope="module")
def accpol_global(topology_st, request):
    """Configure Global account policy plugin and restart the server"""

    log.info('Configuring Global account policy plugin, pwpolicy attributes and restarting the server')
    topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
    try:
        topology_st.standalone.plugins.enable(name=PLUGIN_ACCT_POLICY)
        topology_st.standalone.modify_s(ACCPOL_DN, [(ldap.MOD_REPLACE, 'nsslapd-pluginarg0', ACCP_CONF)])
        topology_st.standalone.modify_s(ACCP_CONF, [(ldap.MOD_REPLACE, 'alwaysrecordlogin', 'yes')])
        topology_st.standalone.modify_s(ACCP_CONF, [(ldap.MOD_REPLACE, 'stateattrname', 'lastLoginTime')])
        topology_st.standalone.modify_s(ACCP_CONF, [(ldap.MOD_REPLACE, 'altstateattrname', 'createTimestamp')])
        topology_st.standalone.modify_s(ACCP_CONF, [(ldap.MOD_REPLACE, 'specattrname', 'acctPolicySubentry')])
        topology_st.standalone.modify_s(ACCP_CONF, [(ldap.MOD_REPLACE, 'limitattrname', 'accountInactivityLimit')])
        topology_st.standalone.modify_s(ACCP_CONF, [(ldap.MOD_REPLACE, 'accountInactivityLimit', '12')])
        topology_st.standalone.config.set('passwordexp', 'on')
        topology_st.standalone.config.set('passwordmaxage', '300')
        topology_st.standalone.config.set('passwordwarning', '1')
        topology_st.standalone.config.set('passwordlockout', 'on')
        topology_st.standalone.config.set('passwordlockoutduration', '10')
        topology_st.standalone.config.set('passwordmaxfailure', '3')
        topology_st.standalone.config.set('passwordunlock', 'on')
    except ldap.LDAPError as e:
        log.error('Failed to enable Global Account Policy Plugin and Password policy attributes')
        raise e
    topology_st.standalone.restart(timeout=10)

    def fin():
        log.info('Disabling Global accpolicy plugin and removing pwpolicy attrs')
        topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
        try:
            topology_st.standalone.plugins.disable(name=PLUGIN_ACCT_POLICY)
            topology_st.standalone.config.set('passwordexp', 'off')
            topology_st.standalone.config.set('passwordlockout', 'off')
        except ldap.LDAPError as e:
            log.error('Failed to disable Global accpolicy plugin, {}'.format(e.message['desc']))
            assert False
        topology_st.standalone.restart(timeout=10)

    request.addfinalizer(fin)


@pytest.fixture(scope="module")
def accpol_local(topology_st, accpol_global, request):
    """Configure Local account policy plugin for ou=people subtree and restart the server"""

    log.info('Adding Local account policy plugin configuration entries')
    topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
    try:
        topology_st.standalone.modify_s(ACCP_CONF, [(ldap.MOD_DELETE, 'accountInactivityLimit', None)])
        topology_st.standalone.config.set('passwordmaxage', '300')
        topology_st.standalone.add_s(Entry((LOCL_CONF, {
            'objectclass': ['top', 'ldapsubentry', 'extensibleObject', 'accountpolicy'],
            'accountInactivityLimit': '10'})))
        topology_st.standalone.add_s(Entry((TEMPL_COS, {
            'objectclass': ['top', 'ldapsubentry', 'extensibleObject', 'cosTemplate'],
            'acctPolicySubentry': LOCL_CONF})))
        topology_st.standalone.add_s(Entry((DEFIN_COS, {
            'objectclass': ['top', 'ldapsubentry', 'cosSuperDefinition', 'cosPointerDefinition'],
            'cosTemplateDn': TEMPL_COS,
            'cosAttribute': 'acctPolicySubentry default operational-default'})))
    except ldap.LDAPError as e:
        log.error('Failed to configure Local account policy plugin')
        log.error('Failed to add entry {}, {}, {}:'.format(LOCL_CONF, TEMPL_COS, DEFIN_COS))
        raise e
    topology_st.standalone.restart(timeout=10)

    def fin():
        log.info('Disabling Local accpolicy plugin and removing pwpolicy attrs')
        topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
        try:
            topology_st.standalone.plugins.disable(name=PLUGIN_ACCT_POLICY)
            topology_st.standalone.delete_s(LOCL_CONF)
            topology_st.standalone.delete_s(TEMPL_COS)
            topology_st.standalone.delete_s(DEFIN_COS)
        except ldap.LDAPError as e:
            log.error('Failed to disable Local accpolicy plugin, {}'.format(e.message['desc']))
            assert False
        topology_st.standalone.restart(timeout=10)

    request.addfinalizer(fin)


def pwacc_lock(topology_st, suffix, subtree, userid, nousrs):
    """Lockout user account by attempting invalid password binds"""

    log.info('Lockout user account by attempting invalid password binds')
    while (nousrs > 0):
        usrrdn = '{}{}'.format(userid, nousrs)
        userdn = 'uid={},{},{}'.format(usrrdn, subtree, suffix)
        for i in range(3):
            with pytest.raises(ldap.INVALID_CREDENTIALS):
                topology_st.standalone.simple_bind_s(userdn, INVL_PASW)
                log.error('No invalid credentials error for User {}'.format(userdn))
        with pytest.raises(ldap.CONSTRAINT_VIOLATION):
            topology_st.standalone.simple_bind_s(userdn, USER_PASW)
            log.error('User {} is not locked, expected error 19'.format(userdn))
        nousrs = nousrs - 1
        time.sleep(1)


def userpw_reset(topology_st, suffix, subtree, userid, nousrs, bindusr, bindpw, newpasw):
    """Reset user password"""

    while (nousrs > 0):
        usrrdn = '{}{}'.format(userid, nousrs)
        userdn = 'uid={},{},{}'.format(usrrdn, subtree, suffix)
        log.info('Reset user password for user-{}'.format(userdn))
        if (bindusr == "DirMgr"):
            topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
            try:
                topology_st.standalone.modify_s(userdn, [(ldap.MOD_REPLACE, 'userPassword', newpasw)])
            except ldap.LDAPError as e:
                log.error('Unable to reset userPassword for user-{}'.format(userdn))
                raise e
        elif (bindusr == "RegUsr"):
            topology_st.standalone.simple_bind_s(userdn, bindpw)
            try:
                topology_st.standalone.modify_s(userdn, [(ldap.MOD_REPLACE, 'userPassword', newpasw)])
            except ldap.LDAPError as e:
                log.error('Unable to reset userPassword for user-{}'.format(userdn))
                raise e
        nousrs = nousrs - 1
        time.sleep(1)


def nsact_inact(topology_st, suffix, subtree, userid, nousrs, command, expected):
    """Account activate/in-activate/status using ns-activate/inactivate/accountstatus.pl"""

    log.info('Account activate/in-activate/status using ns-activate/inactivate/accountstatus.pl')
    while (nousrs > 0):
        usrrdn = '{}{}'.format(userid, nousrs)
        userdn = 'uid={},{},{}'.format(usrrdn, subtree, suffix)
        log.info('Running {} for user {}'.format(command, userdn))
        if ds_is_older('1.3'):
            action = '{}/{}'.format(inst_dir, command)
            try:
                output = subprocess.check_output([action, '-D', DN_DM, '-w', PASSWORD, '-I', userdn])
            except subprocess.CalledProcessError as err:
                output = err.output
        else:
            action = '{}/{}'.format(topology_st.standalone.ds_paths.sbin_dir, command)
            try:
                output = subprocess.check_output(
                    [action, '-Z', SERVERID_STANDALONE, '-D', DN_DM, '-w', PASSWORD, '-I', userdn])
            except subprocess.CalledProcessError as err:
                output = err.output
        log.info('output: {}'.format(output))
        assert expected in output
        nousrs = nousrs - 1
        time.sleep(1)


def modify_attr(topology_st, base_dn, attr_name, attr_val):
    """Modify attribute value for a given DN"""

    log.info('Modify attribute value for a given DN')
    topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
    try:
        topology_st.standalone.modify_s(base_dn, [(ldap.MOD_REPLACE, attr_name, attr_val)])
    except ldap.LDAPError as e:
        log.error('Failed to replace lastLoginTime attribute for user-{} {}'.format(userdn, e.message['desc']))
        assert False
    time.sleep(1)


def check_attr(topology_st, suffix, subtree, userid, nousrs, attr_name):
    """Check ModifyTimeStamp attribute present for user"""

    log.info('Check ModifyTimeStamp attribute present for user')
    topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
    while (nousrs > 0):
        usrrdn = '{}{}'.format(userid, nousrs)
        userdn = 'uid={},{},{}'.format(usrrdn, subtree, suffix)
        try:
            topology_st.standalone.search_s(userdn, ldap.SCOPE_BASE, attr_name)
        except ldap.LDAPError as e:
            log.error('ModifyTimeStamp attribute is not present for user-{} {}'.format(userdn, e.message['desc']))
            assert False
        nousrs = nousrs - 1


def add_time_attr(topology_st, suffix, subtree, userid, nousrs, attr_name):
    """Enable account by replacing lastLoginTime/createTimeStamp/ModifyTimeStamp attribute"""

    new_attr_val = time.strftime("%Y%m%d%H%M%S", time.gmtime()) + 'Z'
    log.info('Enable account by replacing lastLoginTime/createTimeStamp/ModifyTimeStamp attribute')
    topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
    while (nousrs > 0):
        usrrdn = '{}{}'.format(userid, nousrs)
        userdn = 'uid={},{},{}'.format(usrrdn, subtree, suffix)
        try:
            topology_st.standalone.modify_s(userdn, [(ldap.MOD_REPLACE, attr_name, new_attr_val)])
        except ldap.LDAPError as e:
            log.error('Failed to add/replace {} attribute to-{}, for user-{}'.format(attr_name, new_attr_val, userdn))
            raise e
        nousrs = nousrs - 1
        time.sleep(1)
    time.sleep(1)


def modusr_attr(topology_st, suffix, subtree, userid, nousrs, attr_name, attr_value):
    """Enable account by replacing cn attribute value, value of modifyTimeStamp changed"""

    log.info('Enable account by replacing cn attribute value, value of modifyTimeStamp changed')
    topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
    while (nousrs > 0):
        usrrdn = '{}{}'.format(userid, nousrs)
        userdn = 'uid={},{},{}'.format(usrrdn, subtree, suffix)
        try:
            topology_st.standalone.modify_s(userdn, [(ldap.MOD_REPLACE, attr_name, attr_value)])
        except ldap.LDAPError as e:
            log.error('Failed to add/replace {} attribute to-{}, for user-{}'.format(attr_name, attr_value, userdn))
            raise e
        nousrs = nousrs - 1
        time.sleep(1)


def del_time_attr(topology_st, suffix, subtree, userid, nousrs, attr_name):
    """Delete lastLoginTime/createTimeStamp/ModifyTimeStamp attribute from user account"""

    log.info('Delete lastLoginTime/createTimeStamp/ModifyTimeStamp attribute from user account')
    topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
    while (nousrs > 0):
        usrrdn = '{}{}'.format(userid, nousrs)
        userdn = 'uid={},{},{}'.format(usrrdn, subtree, suffix)
        try:
            topology_st.standalone.modify_s(userdn, [(ldap.MOD_DELETE, attr_name, None)])
        except ldap.LDAPError as e:
            log.error('Failed to delete {} attribute for user-{}'.format(attr_name, userdn))
            raise e
        nousrs = nousrs - 1
        time.sleep(1)


def add_users(topology_st, suffix, subtree, userid, nousrs, ulimit):
    """Add users to default test instance with given suffix, subtree, userid and nousrs"""

    log.info('add_users: Pass all of these as parameters suffix, subtree, userid and nousrs')
    users = UserAccounts(topology_st.standalone, suffix, rdn=subtree)
    while (nousrs > ulimit):
        usrrdn = '{}{}'.format(userid, nousrs)
        user_properties = {
            'uid': usrrdn,
            'cn': usrrdn,
            'sn': usrrdn,
            'uidNumber': '1001',
            'gidNumber': '2001',
            'userpassword': USER_PASW,
            'homeDirectory': '/home/{}'.format(usrrdn)}
        users.create(properties=user_properties)
        nousrs = nousrs - 1


def del_users(topology_st, suffix, subtree, userid, nousrs):
    """Delete users from default test instance with given suffix, subtree, userid and nousrs"""

    log.info('del_users: Pass all of these as parameters suffix, subtree, userid and nousrs')
    topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
    users = UserAccounts(topology_st.standalone, suffix, rdn=subtree)
    while (nousrs > 0):
        usrrdn = '{}{}'.format(userid, nousrs)
        userdn = users.get(usrrdn)
        userdn.delete()
        nousrs = nousrs - 1


def account_status(topology_st, suffix, subtree, userid, nousrs, ulimit, tochck):
    """Check account status for the given suffix, subtree, userid and nousrs"""

    while (nousrs > ulimit):
        usrrdn = '{}{}'.format(userid, nousrs)
        userdn = 'uid={},{},{}'.format(usrrdn, subtree, suffix)
        if (tochck == "Enabled"):
            try:
                topology_st.standalone.simple_bind_s(userdn, USER_PASW)
            except ldap.LDAPError as e:
                log.error('User {} failed to login, expected 0'.format(userdn))
                raise e
        elif (tochck == "Expired"):
            with pytest.raises(ldap.INVALID_CREDENTIALS):
                topology_st.standalone.simple_bind_s(userdn, USER_PASW)
                log.error('User {} password not expired , expected error 19'.format(userdn))
        elif (tochck == "Disabled"):
            with pytest.raises(ldap.CONSTRAINT_VIOLATION):
                topology_st.standalone.simple_bind_s(userdn, USER_PASW)
                log.error('User {} is not inactivated, expected error 19'.format(userdn))
        nousrs = nousrs - 1
        time.sleep(1)


def test_glact_inact(topology_st, accpol_global):
    """Verify if user account is inactivated when accountInactivityLimit is exceeded.

    :ID: 342af084-0ad0-442f-b6f6-5a8b8e5e4c28
    :feature: Account Policy Plugin
    :setup: Standalone instance, Global account policy plugin configuration,
            set accountInactivityLimit to few secs.
    :steps: 1. Add few users to ou=people subtree in the default suffix
            2. Check if users are active just before it reaches accountInactivityLimit.
            3. User accounts should not be inactivated, expected 0
            4. Check if users are inactivated when accountInactivityLimit is exceeded.
            5. User accounts should be inactivated, expected error 19.
    :assert: Should return error code 19
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=people"
    userid = "glinactusr"
    nousrs = 3
    log.info('AccountInactivityLimit set to 12. Account will be inactivated if not accessed in 12 secs')
    add_users(topology_st, suffix, subtree, userid, nousrs, 0)
    log.info('Sleep for 10 secs to check if account is not inactivated, expected value 0')
    time.sleep(10)
    log.info('Account should not be inactivated since AccountInactivityLimit not exceeded')
    account_status(topology_st, suffix, subtree, userid, 3, 2, "Enabled")
    log.info('Sleep for 3 more secs to check if account is inactivated')
    time.sleep(3)
    account_status(topology_st, suffix, subtree, userid, 2, 0, "Disabled")
    log.info('Sleep +10 secs to check if account {}3 is inactivated'.format(userid))
    time.sleep(10)
    account_status(topology_st, suffix, subtree, userid, 3, 2, "Disabled")
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_glremv_lastlogin(topology_st, accpol_global):
    """Verify if user account is inactivated by createTimeStamp, if lastLoginTime attribute is missing.

    :ID: 8ded5d8e-ed93-4c22-9c8e-78c479189f84
    :feature: Account Policy Plugin
    :setup: Standalone instance, Global account policy plugin configuration,
            set accountInactivityLimit to few secs.
    :steps: 1. Add few users to ou=people subtree in the default suffix
            2. Wait for few secs and bind as user to create lastLoginTime attribute.
            3. Remove the lastLoginTime attribute from the user.
            4. Wait till accountInactivityLimit exceeded based on createTimeStamp value
            5. Check if users are inactivated, expected error 19.
            6. Replace lastLoginTime attribute and check if account is activated
            7. User should be activated based on lastLoginTime attribute, expected 0
    :assert: Should return error code 19
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=people"
    userid = "nologtimeusr"
    nousrs = 1
    log.info('AccountInactivityLimit set to 12. Account will be inactivated if not accessed in 12 secs')
    add_users(topology_st, suffix, subtree, userid, nousrs, 0)
    log.info('Sleep for 6 secs to check if account is not inactivated, expected value 0')
    time.sleep(6)
    log.info('Account should not be inactivated since AccountInactivityLimit not exceeded')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    del_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    log.info('Sleep for 7 more secs to check if account is inactivated')
    time.sleep(7)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    log.info('Check if account is activated, expected 0')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_glact_login(topology_st, accpol_global):
    """Verify if user account can be activated by replacing the lastLoginTime attribute.

    :ID: f89897cc-c13e-4824-af08-3dd1039bab3c
    :feature: Account Policy Plugin
    :setup: Standalone instance, Global account policy plugin configuration,
            set accountInactivityLimit to few secs.
    :steps: 1. Add few users to ou=groups subtree in the default suffix
            2. Wait till accountInactivityLimit exceeded
            3. Run ldapsearch as normal user, expected error 19.
            4. Replace the lastLoginTime attribute and check if account is activated
            5. Run ldapsearch as normal user, expected 0.
    :assert: Should return success once the user is activated
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=groups"
    userid = "glactusr"
    nousrs = 3
    log.info('AccountInactivityLimit set to 12. Account will be inactivated if not accessed in 12 secs')
    add_users(topology_st, suffix, subtree, userid, nousrs, 0)
    log.info('Sleep for 13 secs to check if account is inactivated, expected error 19')
    time.sleep(13)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    log.info('Check if account is activated, expected 0')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_glinact_limit(topology_st, accpol_global):
    """Verify if account policy plugin functions well when changing accountInactivityLimit value.

    :ID: 7fbc373f-a3d7-4774-8d34-89b057c5e74b
    :feature: Account Policy Plugin
    :setup: Standalone instance, Global account policy plugin configuration,
            set accountInactivityLimit to few secs.
    :steps: 1. Add few users to ou=groups subtree in the default suffix
            2. Check if users are active just before reaching accountInactivityLimit
            3. Modify AccountInactivityLimit to a bigger value
            4. Wait for additional few secs, but check users before it reaches accountInactivityLimit
            5. Wait till accountInactivityLimit exceeded and check users, expected error 19
            6. Modify accountInactivityLimit to use the min value.
            7. Add few users to ou=groups subtree in the default suffix
            8. Wait till it reaches accountInactivityLimit and check users, expected error 19
            9. Modify accountInactivityLimit to 10 times(120 secs) bigger than the initial value.
            10. Add few users to ou=groups subtree in the default suffix
            11. Wait for 90 secs and check if account is not inactivated, expected 0
            12. Wait for +31 secs and check if account is not inactivated, expected 0
            13. Wait for +121 secs and check if account is inactivated, error 19
            14. Replace the lastLoginTime attribute and check if account is activated
            15. Modify accountInactivityLimit to 12 secs, which is the default
            16. Run ldapsearch as normal user, expected 0.
    :assert: Should return success once the user is activated
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=groups"
    userid = "inactestusr"
    nousrs = 3
    log.info('AccountInactivityLimit set to 12. Account will be inactivated if not accessed in 12 secs')
    add_users(topology_st, suffix, subtree, userid, nousrs, 2)
    log.info('Sleep for 9 secs to check if account is not inactivated, expected 0')
    time.sleep(9)
    account_status(topology_st, suffix, subtree, userid, nousrs, 2, "Enabled")
    modify_attr(topology_st, ACCP_CONF, 'accountInactivityLimit', '20')
    time.sleep(18)
    account_status(topology_st, suffix, subtree, userid, nousrs, 2, "Enabled")
    time.sleep(21)
    account_status(topology_st, suffix, subtree, userid, nousrs, 2, "Disabled")
    modify_attr(topology_st, ACCP_CONF, 'accountInactivityLimit', '1')
    add_users(topology_st, suffix, subtree, userid, 2, 1)
    time.sleep(2)
    account_status(topology_st, suffix, subtree, userid, 2, 1, "Disabled")
    modify_attr(topology_st, ACCP_CONF, 'accountInactivityLimit', '120')
    add_users(topology_st, suffix, subtree, userid, 1, 0)
    time.sleep(90)
    account_status(topology_st, suffix, subtree, userid, 1, 0, "Enabled")
    time.sleep(31)
    account_status(topology_st, suffix, subtree, userid, 1, 0, "Enabled")
    time.sleep(121)
    account_status(topology_st, suffix, subtree, userid, 1, 0, "Disabled")
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    log.info('Check if account is activated, expected 0')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    modify_attr(topology_st, ACCP_CONF, 'accountInactivityLimit', '12')
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_glnologin_attr(topology_st, accpol_global):
    """Verify if user account is inactivated based on createTimeStamp attribute, no lastLoginTime attribute present

    :ID: 3032f670-705d-4f69-96f5-d75445cffcfb
    :feature: Account Policy Plugin
    :setup: Standalone instance, Local account policy plugin configuration,
            set accountInactivityLimit to few secs.
    :steps: 1. Configure Global account policy plugin with createTimestamp as stateattrname
            2. lastLoginTime attribute will not be effective.
            3. Add few users to ou=groups subtree in the default suffix
            4. Wait for 10 secs and check if account is not inactivated, expected 0
            5. Modify AccountInactivityLimit to 20 secs
            6. Wait for +9 secs and check if account is not inactivated, expected 0
            7. Wait for +2 secs and check if account is inactivated, error 19
            8. Modify accountInactivityLimit to 2 secs
            9. Add few users to ou=groups subtree in the default suffix
            10. Wait for 3 secs and check if account is inactivated, error 19
            11. Modify accountInactivityLimit to 120 secs
            12. Add few users to ou=groups subtree in the default suffix
            13. Wait for 90 secs and check if account is not inactivated, expected 0
            14. Wait for +29 secs and check if account is not inactivated, expected 0
            15. Wait for +2 secs and check if account is inactivated, error 19
            16. Replace the lastLoginTime attribute and check if account is activated
            17. Modify accountInactivityLimit to 12 secs, which is the default
            18. Run ldapsearch as normal user, expected 0.
    :assert: Should return success once the user is activated
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=groups"
    userid = "nologinusr"
    nousrs = 3
    log.info('AccountInactivityLimit set to 12. Account will be inactivated if not accessed in 12 secs')
    log.info('Set attribute StateAttrName to createTimestamp, loginTime attr wont be considered')
    modify_attr(topology_st, ACCP_CONF, 'stateattrname', 'createTimestamp')
    topology_st.standalone.restart(timeout=10)
    add_users(topology_st, suffix, subtree, userid, nousrs, 2)
    log.info('Sleep for 9 secs to check if account is not inactivated, expected 0')
    time.sleep(9)
    account_status(topology_st, suffix, subtree, userid, nousrs, 2, "Enabled")
    modify_attr(topology_st, ACCP_CONF, 'accountInactivityLimit', '20')
    time.sleep(9)
    account_status(topology_st, suffix, subtree, userid, nousrs, 2, "Enabled")
    time.sleep(3)
    account_status(topology_st, suffix, subtree, userid, nousrs, 2, "Disabled")
    modify_attr(topology_st, ACCP_CONF, 'accountInactivityLimit', '3')
    add_users(topology_st, suffix, subtree, userid, 2, 1)
    time.sleep(2)
    account_status(topology_st, suffix, subtree, userid, 2, 1, "Enabled")
    time.sleep(2)
    account_status(topology_st, suffix, subtree, userid, 2, 1, "Disabled")
    modify_attr(topology_st, ACCP_CONF, 'accountInactivityLimit', '120')
    add_users(topology_st, suffix, subtree, userid, 1, 0)
    time.sleep(90)
    account_status(topology_st, suffix, subtree, userid, 1, 0, "Enabled")
    time.sleep(29)
    account_status(topology_st, suffix, subtree, userid, 1, 0, "Enabled")
    time.sleep(2)
    account_status(topology_st, suffix, subtree, userid, 1, 0, "Disabled")
    modify_attr(topology_st, ACCP_CONF, 'accountInactivityLimit', '12')
    log.info('Set attribute StateAttrName to lastLoginTime, the default')
    modify_attr(topology_st, ACCP_CONF, 'stateattrname', 'lastLoginTime')
    topology_st.standalone.restart(timeout=10)
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    log.info('Check if account is activated, expected 0')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_glnoalt_stattr(topology_st, accpol_global):
    """Verify if user account can be inactivated based on lastLoginTime attribute, altstateattrname set to 1.1

    :ID: 8dcc3540-578f-422a-bb44-28c2cf20dbcd
    :feature: Account Policy Plugin
    :setup: Standalone instance, Global account policy plugin configuration,
            set accountInactivityLimit to few secs.
    :steps: 1. Configure Global account policy plugin with altstateattrname to 1.1
            2. Add few users to ou=groups subtree in the default suffix
            3. Wait till it reaches accountInactivityLimit
            4. Remove lastLoginTime attribute from the user entry
            5. Run ldapsearch as normal user, expected 0. no lastLoginTime attribute present
            6. Wait till it reaches accountInactivityLimit and check users, expected error 19
    :assert: Should return success once the user is activated
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=groups"
    userid = "nologinusr"
    nousrs = 3
    log.info('Set attribute altStateAttrName to 1.1')
    modify_attr(topology_st, ACCP_CONF, 'altstateattrname', '1.1')
    topology_st.standalone.restart(timeout=10)
    add_users(topology_st, suffix, subtree, userid, nousrs, 0)
    log.info('Sleep for 13 secs to check if account is not inactivated, expected 0')
    time.sleep(13)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    log.info('lastLoginTime attribute is added from the above ldap bind by userdn')
    time.sleep(13)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    del_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    modify_attr(topology_st, ACCP_CONF, 'altstateattrname', 'createTimestamp')
    topology_st.standalone.restart(timeout=10)
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_glattr_modtime(topology_st, accpol_global):
    """Verify if user account can be inactivated based on modifyTimeStamp attribute

    :ID: 67380839-2966-45dc-848a-167a954153e1
    :feature: Account Policy Plugin
    :setup: Standalone instance, Global account policy plugin configuration,
            set accountInactivityLimit to few secs.
    :steps: 1. Configure Global account policy plugin with altstateattrname to modifyTimestamp
            2. Add few users to ou=groups subtree in the default suffix
            3. Wait till the accountInactivityLimit exceeded and check users, expected error 19
            4. Modify cn attribute for user, ModifyTimeStamp is updated.
            5. Check if user is activated based on ModifyTimeStamp attribute, expected 0
            6. Change the plugin to use createTimeStamp and remove lastLoginTime attribute
            7. Check if account is inactivated, expected error 19
    :assert: Should return success once the user is activated
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=groups"
    userid = "modtimeusr"
    nousrs = 3
    log.info('Set attribute altStateAttrName to modifyTimestamp')
    modify_attr(topology_st, ACCP_CONF, 'altstateattrname', 'modifyTimestamp')
    topology_st.standalone.restart(timeout=10)
    add_users(topology_st, suffix, subtree, userid, nousrs, 0)
    log.info('Sleep for 13 secs to check if account is inactivated, expected 0')
    time.sleep(13)
    check_attr(topology_st, suffix, subtree, userid, nousrs, "modifyTimeStamp=*")
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    attr_name = "cn"
    attr_value = "cnewusr"
    modusr_attr(topology_st, suffix, subtree, userid, nousrs, attr_name, attr_value)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    modify_attr(topology_st, ACCP_CONF, 'altstateattrname', 'createTimestamp')
    del_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    topology_st.standalone.restart(timeout=10)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_glnoalt_nologin(topology_st, accpol_global):
    """Verify if account policy plugin works if we set altstateattrname set to 1.1 and alwaysrecordlogin to NO

    :ID: 49eda7db-84de-47ba-8f81-ac5e4de3a500
    :feature: Account Policy Plugin
    :setup: Standalone instance, Global account policy plugin configuration,
            set accountInactivityLimit to few secs.
    :steps: 1. Configure Global account policy plugin with altstateattrname to 1.1
            2. Set alwaysrecordlogin to NO.
            3. Add few users to ou=groups subtree in the default suffix
            4. Wait till accountInactivityLimit exceeded and check users, expected 0
            5. Check for lastLoginTime attribute, it should not be present
            6. Wait for few more secs and check if account is not inactivated, expected 0
            7. Run ldapsearch as normal user, expected 0. no lastLoginTime attribute present
            8. Set altstateattrname to createTimeStamp
            9. Check if user account is inactivated based on createTimeStamp attribute.
            10. Account should be inactivated, expected error 19
    :assert: Should return success once the user is activated
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=groups"
    userid = "norecrodlogusr"
    nousrs = 3
    log.info('Set attribute altStateAttrName to 1.1')
    modify_attr(topology_st, ACCP_CONF, 'altstateattrname', '1.1')
    log.info('Set attribute alwaysrecordlogin to No')
    modify_attr(topology_st, ACCP_CONF, 'alwaysrecordlogin', 'no')
    topology_st.standalone.restart(timeout=10)
    add_users(topology_st, suffix, subtree, userid, nousrs, 0)
    log.info('Sleep for 13 secs to check if account is not inactivated, expected 0')
    time.sleep(13)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    time.sleep(3)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    log.info('Set attribute altStateAttrName to createTimestamp')
    modify_attr(topology_st, ACCP_CONF, 'altstateattrname', 'createTimestamp')
    topology_st.standalone.restart(timeout=10)
    time.sleep(2)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    log.info('Reset the default attribute values')
    modify_attr(topology_st, ACCP_CONF, 'alwaysrecordlogin', 'yes')
    topology_st.standalone.restart(timeout=10)
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_glinact_nsact(topology_st, accpol_global):
    """Verify if user account can be activated using ns-activate.pl script.

    :ID: 876a7a7c-0b3f-4cd2-9b45-1dc80846e334
    :feature: Account Policy Plugin
    :setup: Standalone instance, Global account policy plugin configuration,
            set accountInactivityLimit to few secs.
    :steps: 1. Configure Global account policy plugin
            2. Add few users to ou=groups subtree in the default suffix
            3. Wait for few secs and inactivate user using ns-inactivate.pl
            4. Wait till accountInactivityLimit exceeded.
            5. Run ldapsearch as normal user, expected error 19.
            6. Activate user using ns-activate.pl script
            7. Check if account is activated, expected error 19
            8. Replace the lastLoginTime attribute and check if account is activated
            9. Run ldapsearch as normal user, expected 0.
    :assert: Should return success once the user is activated
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=groups"
    userid = "nsactusr"
    nousrs = 1
    log.info('AccountInactivityLimit set to 12. Account will be inactivated if not accessed in 12 secs')
    add_users(topology_st, suffix, subtree, userid, nousrs, 0)
    log.info('Sleep for 3 secs to check if account is not inactivated, expected value 0')
    time.sleep(3)
    nsact_inact(topology_st, suffix, subtree, userid, nousrs, "ns-activate.pl", "")
    log.info('Sleep for 10 secs to check if account is inactivated, expected value 19')
    time.sleep(10)
    nsact_inact(topology_st, suffix, subtree, userid, nousrs, "ns-activate.pl", "")
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    nsact_inact(topology_st, suffix, subtree, userid, nousrs, "ns-accountstatus.pl",
                "- inactivated (inactivity limit exceeded)")
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    nsact_inact(topology_st, suffix, subtree, userid, nousrs, "ns-accountstatus.pl", "- activated")
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_glinact_acclock(topology_st, accpol_global):
    """Verify if user account is activated when account is unlocked by passwordlockoutduration.

    :ID: 43601a61-065c-4c80-a7c2-e4f6ae17beb8
    :feature: Account Policy Plugin
    :setup: Standalone instance, Global account policy plugin configuration,
            set accountInactivityLimit to few secs.
    :steps: 1. Add few users to ou=groups subtree in the default suffix
            2. Wait for few secs and attempt invalid binds for user
            3. User account should be locked based on Account Lockout policy.
            4. Wait till accountInactivityLimit exceeded and check users, expected error 19
            5. Wait for passwordlockoutduration and check if account is active
            6. Check if account is unlocked, expected error 19, since account is inactivated
            7. Replace the lastLoginTime attribute and check users, expected 0
    :assert: Should return success once the user is activated
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=groups"
    userid = "pwlockusr"
    nousrs = 1
    log.info('AccountInactivityLimit set to 12. Account will be inactivated if not accessed in 12 secs')
    add_users(topology_st, suffix, subtree, userid, nousrs, 0)
    log.info('Sleep for 3 secs and try invalid binds to lockout the user')
    time.sleep(3)
    pwacc_lock(topology_st, suffix, subtree, userid, nousrs)
    log.info('Sleep for 10 secs to check if account is inactivated, expected value 19')
    time.sleep(10)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    log.info('Add lastLoginTime to activate the user account')
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    log.info(
        'Checking if account is unlocked after passwordlockoutduration, but inactivated after accountInactivityLimit')
    pwacc_lock(topology_st, suffix, subtree, userid, nousrs)
    log.info('Account is expected to be unlocked after 10 secs of passwordlockoutduration')
    log.info('Sleep for 10 secs to check if account is not inactivated, expected value 0')
    time.sleep(10)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    log.info('Sleep 13s and check if account inactivated based on accountInactivityLimit, expected 19')
    time.sleep(13)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_glnact_pwexp(topology_st, accpol_global):
    """Verify if user account is activated when password is reset after password is expired

    :ID:  3bb97992-101a-4e5a-b60a-4cc21adcc76e
    :feature: Account Policy Plugin
    :setup: Standalone instance, Global account policy plugin configuration,
            set accountInactivityLimit to few secs.
    :steps: 1. Add few users to ou=groups subtree in the default suffix
            2. Set passwordmaxage to few secs
            3. Wait for passwordmaxage to reach and check if password expired
            4. Run ldapsearch as normal user, expected error 19.
            5. Reset the password for user account
            6. Wait till accountInactivityLimit exceeded and check users
            7. Run ldapsearch as normal user, expected error 19.
            8. Replace the lastLoginTime attribute and check if account is activated
            9. Run ldapsearch as normal user, expected 0.
    :assert: Should return success once the user is activated
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=groups"
    userid = "pwexpusr"
    nousrs = 1
    topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
    try:
        topology_st.standalone.config.set('passwordmaxage', '9')
    except ldap.LDAPError as e:
        log.error('Failed to change the value of passwordmaxage to 9')
        raise e
    log.info('AccountInactivityLimit set to 12. Account will be inactivated if not accessed in 12 secs')
    log.info('Passwordmaxage is set to 9. Password will expire in 9 secs')
    add_users(topology_st, suffix, subtree, userid, nousrs, 0)
    log.info('Sleep for 9 secs and check if password expired')
    time.sleep(9)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Expired")
    time.sleep(4)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    log.info('Add lastLoginTime to activate the user account')
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Expired")
    userpw_reset(topology_st, suffix, subtree, userid, nousrs, "DirMgr", PASSWORD, USER_PASW)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    time.sleep(7)
    userpw_reset(topology_st, suffix, subtree, userid, nousrs, "DirMgr", PASSWORD, USER_PASW)
    log.info('Sleep for 6 secs and check if account is inactivated, expected error 19')
    time.sleep(6)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    time.sleep(4)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Expired")
    userpw_reset(topology_st, suffix, subtree, userid, nousrs, "DirMgr", PASSWORD, USER_PASW)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
    try:
        topology_st.standalone.config.set('passwordmaxage', '300')
    except ldap.LDAPError as e:
        log.error('Failed to change the value of passwordmaxage to 300')
        raise e
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_locact_inact(topology_st, accpol_local):
    """Verify if user account is inactivated when accountInactivityLimit is exceeded.

    :ID: 02140e36-79eb-4d88-ba28-66478689289b
    :feature: Account Policy Plugin
    :setup: Standalone instance, ou=people subtree configured for Local account
            policy plugin configuration, set accountInactivityLimit to few secs.
    :steps: 1. Add few users to ou=people subtree in the default suffix
            2. Wait for few secs before it reaches accountInactivityLimit and check users.
            3. Run ldapsearch as normal user, expected 0
            4. Wait till accountInactivityLimit is exceeded
            5. Run ldapsearch as normal user and check if its inactivated, expected error 19.
            6. Replace user's lastLoginTime attribute and check if its activated, expected 0
    :assert: Should return error code 19
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=people"
    userid = "inactusr"
    nousrs = 3
    log.info('AccountInactivityLimit set to 10. Account will be inactivated if not accessed in 10 secs')
    add_users(topology_st, suffix, subtree, userid, nousrs, 0)
    log.info('Sleep for 9 secs to check if account is not inactivated, expected value 0')
    time.sleep(9)
    log.info('Account should not be inactivated since AccountInactivityLimit not exceeded')
    account_status(topology_st, suffix, subtree, userid, 3, 2, "Enabled")
    log.info('Sleep for 2 more secs to check if account is inactivated')
    time.sleep(2)
    account_status(topology_st, suffix, subtree, userid, 2, 0, "Disabled")
    log.info('Sleep +9 secs to check if account {}3 is inactivated'.format(userid))
    time.sleep(9)
    account_status(topology_st, suffix, subtree, userid, 3, 2, "Disabled")
    log.info('Add lastLoginTime attribute to all users and check if its activated')
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_locinact_modrdn(topology_st, accpol_local):
    """Verify if user account is inactivated when moved from ou=groups to ou=people subtree.

    :ID: 5f25bea3-fab0-4db4-b43d-2d47cc6e5ad1
    :feature: Account Policy Plugin
    :setup: Standalone instance, ou=people subtree configured for Local account
            policy plugin configuration, set accountInactivityLimit to few secs.
    :steps: 1. Add few users to ou=groups subtree in the default suffix
            2. Plugin configured to ou=people subtree only.
            3. Wait for few secs before it reaches accountInactivityLimit and check users.
            4. Run ldapsearch as normal user, expected 0
            5. Wait till accountInactivityLimit exceeded
            6. Move users from ou=groups subtree to ou=people subtree
            7. Check if users are inactivated, expected error 19
    :assert: Should return error code 0 and 19
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=groups"
    userid = "nolockusr"
    nousrs = 1
    log.info('Account should not be inactivated since the subtree is not configured')
    add_users(topology_st, suffix, subtree, userid, nousrs, 0)
    log.info('Sleep for 11 secs to check if account is not inactivated, expected value 0')
    time.sleep(11)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    log.info('Moving users from ou=groups to ou=people subtree')
    topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
    try:
        topology_st.standalone.rename_s('uid=nolockusr1,ou=groups,dc=example,dc=com', 'uid=nolockusr1',
                                        'ou=people,dc=example,dc=com')
    except ldap.LDAPError as e:
        log.error('Failed to move user uid=nolockusr1 from ou=groups to ou=people')
        raise e
    subtree = "ou=people"
    log.info('Then wait for 11 secs and check if entries are inactivated')
    time.sleep(11)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    add_time_attr(topology_st, suffix, subtree, userid, nousrs, 'lastLoginTime')
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Enabled")
    del_users(topology_st, suffix, subtree, userid, nousrs)


def test_locact_modrdn(topology_st, accpol_local):
    """Verify if user account is inactivated when users moved from ou=people to ou=groups subtree.

    :ID: e821cbae-bfc3-40d3-947d-b228c809987f
    :feature: Account Policy Plugin
    :setup: Standalone instance, ou=people subtree configured for Local account
            policy plugin configuration, set accountInactivityLimit to few secs.
    :steps: 1. Add few users to ou=people subtree in the default suffix
            2. Wait for few secs and check if users not inactivated, expected 0.
            3. Move users from ou=people to ou=groups subtree
            4. Wait till accountInactivityLimit is exceeded
            5. Check if users are active in ou=groups subtree, expected 0
    :assert: Should return error code 0
    """

    suffix = DEFAULT_SUFFIX
    subtree = "ou=people"
    userid = "lockusr"
    nousrs = 1
    log.info('Account should be inactivated since the subtree is configured')
    add_users(topology_st, suffix, subtree, userid, nousrs, 0)
    log.info('Sleep for 11 secs to check if account is inactivated, expected value 19')
    time.sleep(11)
    account_status(topology_st, suffix, subtree, userid, nousrs, 0, "Disabled")
    log.info('Moving users from ou=people to ou=groups subtree')
    topology_st.standalone.simple_bind_s(DN_DM, PASSWORD)
    try:
        topology_st.standalone.rename_s('uid=lockusr1,ou=people,dc=example,dc=com', 'uid=lockusr1',
                                        'ou=groups,dc=example,dc=com')
    except ldap.LDAPError as e:
        log.error('Failed to move user uid=lockusr1 from ou=groups to ou=people')
        raise e
    log.info('Sleep for +2 secs and check users from both ou=people and ou=groups subtree')
    time.sleep(2)
    subtree = "ou=groups"
    account_status(topology_st, suffix, subtree, userid, 1, 0, "Enabled")
    del_users(topology_st, suffix, subtree, userid, nousrs)


if __name__ == '__main__':
    # Run isolated
    # -s for DEBUG mode
    CURRENT_FILE = os.path.realpath(__file__)
    pytest.main("-s {}".format(CURRENT_FILE))
