/** BEGIN COPYRIGHT BLOCK
 * Copyright (C) 2001 Sun Microsystems, Inc. Used by permission.
 * Copyright (C) 2005 Red Hat, Inc.
 * All rights reserved.
 *
 * License: GPL (version 3 or any later version).
 * See LICENSE for details.
 * END COPYRIGHT BLOCK **/

#ifdef HAVE_CONFIG_H
#include <config.h>
#endif

/*
 * Copyright (c) 1995 Regents of the University of Michigan.
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms are permitted
 * provided that this notice is preserved and that due credit is given
 * to the University of Michigan at Ann Arbor. The name of the University
 * may not be used to endorse or promote products derived from this
 * software without specific prior written permission. This software
 * is provided ``as is'' without express or implied warranty.
 */

#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#include <sys/socket.h>
#include "slap.h"
#include "pratom.h"

/* Forward declarations */
static int rename_internal_pb(Slapi_PBlock *pb);
static void op_shared_rename(Slapi_PBlock *pb, int passin_args);
static int check_rdn_for_created_attrs(const char *newrdn);

/* This function is called to process operation that come over external connections */
void
do_modrdn(Slapi_PBlock *pb)
{
    Slapi_Operation *operation;
    Connection *pb_conn;
    BerElement *ber;
    char *rawdn = NULL, *rawnewsuperior = NULL;
    const char *dn = NULL, *newsuperior = NULL;
    char *newrdn = NULL;
    int err = 0, deloldrdn = 0;
    ber_len_t len = 0;
    char *newdn = NULL;
    char *parent = NULL;
    Slapi_DN sdn;
    Slapi_DN snewdn;
    Slapi_DN *snewsuperior = NULL;

    slapi_log_err(SLAPI_LOG_TRACE, "do_modrdn", "=>\n");

    /* count the modrdn request */
    slapi_counter_increment(g_get_per_thread_snmp_vars()->ops_tbl.dsModifyRDNOps);

    slapi_pblock_get(pb, SLAPI_OPERATION, &operation);
    ber = operation->o_ber;
    slapi_pblock_get(pb, SLAPI_CONNECTION, &pb_conn);

    slapi_sdn_init(&sdn);
    slapi_sdn_init(&snewdn);

    /*
     * Parse the modrdn request.  It looks like this:
     *
     *    ModifyRDNRequest := SEQUENCE {
     *        entry            DistinguishedName,
     *        newrdn            RelativeDistinguishedName,
     *        deleteoldrdn        BOOLEAN,
     *        newSuperior    [0] LDAPDN OPTIONAL -- v3 only
     *    }
     */

    if (ber_scanf(ber, "{aab", &rawdn, &newrdn, &deloldrdn) == LBER_ERROR) {
        slapi_log_err(SLAPI_LOG_ERR, "do_modrdn",
                      "ber_scanf failed (op=ModRDN; params=DN,newRDN,deleteOldRDN)\n");
        op_shared_log_error_access(pb, "MODRDN", "???", "decoding error");
        send_ldap_result(pb, LDAP_PROTOCOL_ERROR, NULL,
                         "unable to decode DN, newRDN, or deleteOldRDN parameters",
                         0, NULL);
        goto free_and_return;
    }

    if (ber_peek_tag(ber, &len) == LDAP_TAG_NEWSUPERIOR) {
        /* This "len" is not used... */
        if (pb_conn->c_ldapversion < LDAP_VERSION3) {
            slapi_log_err(SLAPI_LOG_ERR, "do_modrdn",
                          "Got newSuperior in LDAPv2 modrdn op\n");
            op_shared_log_error_access(pb, "MODRDN",
                                       rawdn ? rawdn : "", "decoding error");
            send_ldap_result(pb, LDAP_PROTOCOL_ERROR, NULL,
                             "received newSuperior in LDAPv2 modrdn", 0, NULL);
            slapi_ch_free_string(&rawdn);
            slapi_ch_free_string(&newrdn);
            goto free_and_return;
        }
        if (ber_scanf(ber, "a", &rawnewsuperior) == LBER_ERROR) {
            slapi_log_err(SLAPI_LOG_ERR, "do_modrdn",
                          "ber_scanf failed (op=ModRDN; params=newSuperior)\n");
            op_shared_log_error_access(pb, "MODRDN", rawdn, "decoding error");
            send_ldap_result(pb, LDAP_PROTOCOL_ERROR, NULL,
                             "unable to decode newSuperior parameter", 0, NULL);
            slapi_ch_free_string(&rawdn);
            slapi_ch_free_string(&newrdn);
            goto free_and_return;
        }
    }

    /* Check if we should be performing strict validation. */
    if (config_get_dn_validate_strict()) {
        /* check that the dn is formatted correctly */
        err = slapi_dn_syntax_check(pb, rawdn, 1);
        if (err) { /* syntax check failed */
            op_shared_log_error_access(pb, "MODRDN", rawdn ? rawdn : "",
                                       "strict: invalid dn");
            send_ldap_result(pb, LDAP_INVALID_DN_SYNTAX,
                             NULL, "invalid dn", 0, NULL);
            slapi_ch_free_string(&rawdn);
            slapi_ch_free_string(&newrdn);
            slapi_ch_free_string(&rawnewsuperior);
            goto free_and_return;
        }
        /* check that the new rdn is formatted correctly */
        err = slapi_dn_syntax_check(pb, newrdn, 1);
        if (err) { /* syntax check failed */
            op_shared_log_error_access(pb, "MODRDN", newrdn ? newrdn : "",
                                       "strict: invalid new rdn");
            send_ldap_result(pb, LDAP_INVALID_DN_SYNTAX,
                             NULL, "invalid new rdn", 0, NULL);
            slapi_ch_free_string(&rawdn);
            slapi_ch_free_string(&newrdn);
            slapi_ch_free_string(&rawnewsuperior);
            goto free_and_return;
        }
    }
    slapi_sdn_init_dn_passin(&sdn, rawdn);
    dn = slapi_sdn_get_dn(&sdn);
    if (rawdn && (strlen(rawdn) > 0) && (NULL == dn)) {
        /* normalization failed */
        op_shared_log_error_access(pb, "MODRDN", rawdn, "invalid dn");
        send_ldap_result(pb, LDAP_INVALID_DN_SYNTAX, NULL,
                         "invalid dn", 0, NULL);
        slapi_ch_free_string(&newrdn);
        slapi_ch_free_string(&rawnewsuperior);
        goto free_and_return;
    }

    if (rawnewsuperior) {
        if (config_get_dn_validate_strict()) {
            /* check that the dn is formatted correctly */
            err = slapi_dn_syntax_check(pb, rawnewsuperior, 1);
            if (err) { /* syntax check failed */
                op_shared_log_error_access(pb, "MODRDN", rawnewsuperior,
                                           "strict: invalid new superior");
                send_ldap_result(pb, LDAP_INVALID_DN_SYNTAX,
                                 NULL, "invalid new superior", 0, NULL);
                slapi_ch_free_string(&rawnewsuperior);
                goto free_and_return;
            }
        }
        snewsuperior = slapi_sdn_new_dn_passin(rawnewsuperior);
        newsuperior = slapi_sdn_get_dn(snewsuperior);
    }

    /*
     * If newsuperior is myself or my descendent, the modrdn should fail.
     * Note: need to check the case newrdn is given, and newsuperior
     * uses the newrdn, as well.
     */
    parent = slapi_dn_parent(slapi_sdn_get_ndn(&sdn));
    newdn = slapi_ch_smprintf("%s,%s", newrdn, parent);
    /* slapi_sdn_init_normdn_passin expects normalized but NOT
     * decapitalized dn */
    slapi_sdn_init_dn_passin(&snewdn, newdn);
    if (0 == slapi_sdn_compare(&sdn, snewsuperior) ||
        0 == slapi_sdn_compare(&snewdn, snewsuperior)) {
        op_shared_log_error_access(pb, "MODRDN", newsuperior,
                                   "new superior is identical to the entry dn");
        send_ldap_result(pb, LDAP_UNWILLING_TO_PERFORM, NULL,
                         "new superior is identical to the entry dn", 0, NULL);
        goto free_and_return;
    }
    if (slapi_sdn_issuffix(snewsuperior, &sdn) ||
        slapi_sdn_issuffix(snewsuperior, &snewdn)) {
        /* E.g.,
         * newsuperior: ou=sub,ou=people,dc=example,dc=com
         * dn: ou=people,dc=example,dc=com
         */
        op_shared_log_error_access(pb, "MODRDN", newsuperior,
                                   "new superior is descendent of the entry");
        send_ldap_result(pb, LDAP_UNWILLING_TO_PERFORM, NULL,
                         "new superior is descendent of the entry", 0, NULL);
        goto free_and_return;
    }

    /*
     * in LDAPv3 there can be optional control extensions on
     * the end of an LDAPMessage. we need to read them in and
     * pass them to the backend.
     */
    if ((err = get_ldapmessage_controls(pb, ber, NULL)) != 0) {
        op_shared_log_error_access(pb, "MODRDN", dn, "failed to decode LDAP controls");
        send_ldap_result(pb, err, NULL, NULL, 0, NULL);
        goto free_and_return;
    }

    slapi_log_err(SLAPI_LOG_ARGS,
                  "do_modrd", "dn (%s) newrdn (%s) deloldrdn (%d)\n", dn, newrdn,
                  deloldrdn);

    slapi_pblock_set(pb, SLAPI_REQUESTOR_ISROOT, &operation->o_isroot);
    /* dn, newrdn and newsuperior are all normalized */
    slapi_pblock_set(pb, SLAPI_ORIGINAL_TARGET,
                     (void *)slapi_sdn_get_udn(&sdn));
    slapi_pblock_set(pb, SLAPI_MODRDN_TARGET_SDN, &sdn);
    slapi_pblock_set(pb, SLAPI_MODRDN_NEWRDN, (void *)newrdn);
    slapi_pblock_set(pb, SLAPI_MODRDN_NEWSUPERIOR_SDN, (void *)snewsuperior);
    slapi_pblock_set(pb, SLAPI_MODRDN_DELOLDRDN, &deloldrdn);

    op_shared_rename(pb, 0 /* do not pass in ownership of string arguments */);

free_and_return:
    slapi_sdn_done(&sdn);
    slapi_ch_free_string(&newrdn);
    slapi_sdn_free(&snewsuperior);
    slapi_sdn_done(&snewdn);
    slapi_ch_free_string(&parent);

    return;
}

/* This function is used to issue internal modrdn operation
   This is an old style API. Its use is discouraged because it is not extendable and
   because it does not allow to check whether plugin has right to access part of the
   tree it is trying to modify. Use slapi_modrdn_internal_pb instead */
Slapi_PBlock *
slapi_modrdn_internal(const char *iodn, const char *inewrdn, int deloldrdn, LDAPControl **controls, int dummy)
{
    return slapi_rename_internal(iodn, inewrdn, NULL, deloldrdn, controls, dummy);
}

Slapi_PBlock *
slapi_rename_internal(const char *iodn, const char *inewrdn, const char *inewsuperior, int deloldrdn, LDAPControl **controls, int dummy __attribute__((unused)))
{
    Slapi_PBlock *pb = slapi_pblock_new();
    Slapi_PBlock *result_pb = NULL;
    int opresult = 0;
    Slapi_DN sdn;
    Slapi_DN newsuperiorsdn;

    slapi_sdn_init_dn_byref(&sdn, iodn);
    slapi_sdn_init_dn_byref(&newsuperiorsdn, inewsuperior);

    slapi_rename_internal_set_pb_ext(pb, &sdn, inewrdn, &newsuperiorsdn,
                                     deloldrdn, controls, NULL,
                                     plugin_get_default_component_id(), 0);
    rename_internal_pb(pb);

    result_pb = slapi_pblock_new();
    if (result_pb) {
        slapi_pblock_get(pb, SLAPI_PLUGIN_INTOP_RESULT, &opresult);
        slapi_pblock_set(result_pb, SLAPI_PLUGIN_INTOP_RESULT, &opresult);
    }

    slapi_sdn_done(&sdn);
    slapi_sdn_done(&newsuperiorsdn);
    slapi_pblock_destroy(pb);

    return result_pb;
}

/*    This is new style API to issue internal add operation.
    pblock should contain the following data (can be set via call to slapi_rename_internal_set_pb):
    For uniqueid based operation:
        SLAPI_TARGET_DN set to dn that allows to select right backend, can be stale
        SLAPI_TARGET_UNIQUEID set to the uniqueid of the entry we are looking for
        SLAPI_MODRDN_NEWRDN set to new rdn of the entry
        SLAPI_MODRDN_DELOLDRDN tells whether old rdn should be kept in the entry
        LAPI_CONTROLS_ARG set to request controls if present

    For dn based search:
        SLAPI_TARGET_DN set to the entry dn
        SLAPI_MODRDN_NEWRDN set to new rdn of the entry
        SLAPI_MODRDN_DELOLDRDN tells whether old rdn should be kept in the entry
        SLAPI_CONTROLS_ARG set to request controls if present
 */
int
slapi_modrdn_internal_pb(Slapi_PBlock *pb)
{
    if (pb == NULL)
        return -1;

    return rename_internal_pb(pb);
}

void
slapi_rename_internal_set_pb_ext(Slapi_PBlock *pb,
                                 const Slapi_DN *olddn,
                                 const char *newrdn, /* normalized */
                                 const Slapi_DN *newsuperior,
                                 int deloldrdn,
                                 LDAPControl **controls,
                                 const char *uniqueid,
                                 Slapi_ComponentId *plugin_identity,
                                 int operation_flags)
{
    Operation *op;
    PR_ASSERT(pb != NULL);
    if (pb == NULL || olddn == NULL || newrdn == NULL) {
        slapi_log_err(SLAPI_LOG_ERR, "slapi_rename_internal_set_pb_ext",
                      "NULL parameter\n");
        return;
    }

    op = internal_operation_new(SLAPI_OPERATION_MODRDN, operation_flags);
    slapi_pblock_set(pb, SLAPI_OPERATION, op);
    slapi_pblock_set(pb, SLAPI_ORIGINAL_TARGET,
                     (void *)slapi_sdn_get_dn(olddn));
    slapi_pblock_set(pb, SLAPI_MODRDN_TARGET_SDN, (void *)olddn);
    slapi_pblock_set(pb, SLAPI_MODRDN_NEWRDN, (void *)newrdn);
    slapi_pblock_set(pb, SLAPI_MODRDN_NEWSUPERIOR_SDN, (void *)newsuperior);
    slapi_pblock_set(pb, SLAPI_MODRDN_DELOLDRDN, &deloldrdn);
    slapi_pblock_set(pb, SLAPI_CONTROLS_ARG, controls);
    slapi_pblock_set(pb, SLAPI_MODIFY_MODS, NULL);
    if (uniqueid) {
        slapi_pblock_set(pb, SLAPI_TARGET_UNIQUEID, (void *)uniqueid);
    }
    slapi_pblock_set(pb, SLAPI_PLUGIN_IDENTITY, plugin_identity);
}

/* Helper functions */

static int
rename_internal_pb(Slapi_PBlock *pb)
{
    LDAPControl **controls;
    Operation *op;
    int opresult = 0;

    PR_ASSERT(pb != NULL);

    slapi_pblock_get(pb, SLAPI_CONTROLS_ARG, &controls);
    slapi_pblock_get(pb, SLAPI_OPERATION, &op);
    if (!op) {
        opresult = 1;
        slapi_pblock_set(pb, SLAPI_PLUGIN_INTOP_RESULT, &opresult);
        slapi_log_err(SLAPI_LOG_ERR, "rename_internal_pb",
                      "Internal error: pblock was not properly initialized\n");
        return -1;
    }
    op->o_handler_data = &opresult;
    op->o_result_handler = internal_getresult_callback;

    slapi_pblock_set(pb, SLAPI_REQCONTROLS, controls);

    /* set parameters common for all internal operations */
    set_common_params(pb);

    /* set actions taken to process the operation */
    set_config_params(pb);

    slapi_td_internal_op_start();
    op_shared_rename(pb, 0 /* not passing ownership of args */);
    slapi_td_internal_op_finish();

    slapi_pblock_set(pb, SLAPI_PLUGIN_INTOP_RESULT, &opresult);

    return 0;
}


/*
 * op_shared_rename() -- common frontend code for modDN operations.
 *
 * Beware: this function resets the following pblock elements that were
 * set by the caller:
 *
 *    SLAPI_MODRDN_TARGET_SDN
 *    SLAPI_MODRDN_NEWRDN
 *    SLAPI_MODRDN_NEWSUPERIOR_SDN
 */
static void
op_shared_rename(Slapi_PBlock *pb, int passin_args)
{
    char *dn, *newrdn, *newdn = NULL;
    const char *newsuperior;
    char **rdns;
    int deloldrdn;
    Slapi_Backend *be = NULL;
    Slapi_DN *origsdn = NULL;
    Slapi_Mods smods;
    int internal_op, repl_op, lastmod;
    Slapi_Operation *operation;
    Slapi_Entry *referral;
    char errorbuf[SLAPI_DSE_RETURNTEXT_SIZE];
    int err;
    char *proxydn = NULL;
    char *proxystr = NULL;
    int proxy_err = LDAP_SUCCESS;
    char *errtext = NULL;
    Slapi_DN *sdn = NULL;
    Slapi_DN *newsuperiorsdn = NULL;
    Connection *pb_conn;

    slapi_pblock_get(pb, SLAPI_ORIGINAL_TARGET, &dn);
    slapi_pblock_get(pb, SLAPI_MODRDN_NEWRDN, &newrdn);
    slapi_pblock_get(pb, SLAPI_MODRDN_NEWSUPERIOR_SDN, &newsuperiorsdn);
    slapi_pblock_get(pb, SLAPI_MODRDN_DELOLDRDN, &deloldrdn);
    slapi_pblock_get(pb, SLAPI_IS_REPLICATED_OPERATION, &repl_op);
    slapi_pblock_get(pb, SLAPI_OPERATION, &operation);
    slapi_pblock_get(pb, SLAPI_MODRDN_TARGET_SDN, &origsdn);
    internal_op = operation_is_flag_set(operation, OP_FLAG_INTERNAL);
    slapi_pblock_get(pb, SLAPI_CONNECTION, &pb_conn);

    /* Set the time we actually started the operation */
    slapi_operation_set_time_started(operation);

    /*
     * If ownership has not been passed to this function, we replace the
     * string input fields within the pblock with strdup'd copies.  Why?
     * Because some pre- and post-op plugins may change them, and the
     * convention is that plugins should place a malloc'd string in the
     * pblock.  Therefore, we need to be able to retrieve and free them
     * later.  But the callers of the internal modrdn calls are promised
     * that we will not free these parameters... so if passin_args is
     * zero, we need to make copies.
     *
     * In the case of SLAPI_MODRDN_TARGET_SDN and SLAPI_MODRDN_NEWSUPERIOR_SDN,
     * we replace the existing values with normalized values (because plugins
     * expect these DNs to be normalized).
     */

    if (NULL == origsdn) {
        sdn = slapi_sdn_new_dn_byval(dn);
        slapi_pblock_set(pb, SLAPI_MODRDN_TARGET_SDN, sdn);
    }
    if (passin_args) {
        if (NULL == sdn) { /* origsdn is not NULL, so use it. */
            sdn = origsdn;
        }
    } else {
        if (NULL == sdn) {
            sdn = slapi_sdn_dup(origsdn);
        }
        newrdn = slapi_ch_strdup(newrdn);
        newsuperiorsdn = slapi_sdn_dup(newsuperiorsdn);
        slapi_pblock_set(pb, SLAPI_MODRDN_TARGET_SDN, sdn);
        slapi_pblock_set(pb, SLAPI_MODRDN_NEWRDN, (void *)newrdn);
        slapi_pblock_set(pb, SLAPI_MODRDN_NEWSUPERIOR_SDN, newsuperiorsdn);
    }
    /* normdn = slapi_sdn_get_dn(sdn); */
    newsuperior = slapi_sdn_get_dn(newsuperiorsdn);

    /* get the proxy auth dn if the proxy auth control is present */
    proxy_err = proxyauth_get_dn(pb, &proxydn, &errtext);

    /*
     * first, log the operation to the access log,
     * then check rdn and newsuperior,
     * and - if applicable - log reason of any error to the errors log
     */
    if (operation_is_flag_set(operation, OP_FLAG_ACTION_LOG_ACCESS)) {
        int32_t log_format = config_get_accesslog_log_format();
        slapd_log_pblock logpb = {0};

        slapd_log_pblock_init(&logpb, log_format, pb);
        logpb.target_dn = dn;
        logpb.newrdn = newrdn;
        logpb.newsup = newsuperior;
        logpb.deleteoldrdn = deloldrdn ? PR_TRUE : PR_FALSE;
        logpb.authzid = proxydn;

        if (proxydn) {
            proxystr = slapi_ch_smprintf(" authzid=\"%s\"", proxydn);
        }

        if (!internal_op) {
            if (log_format != LOG_FORMAT_DEFAULT) {
                slapd_log_access_modrdn(&logpb);
            } else {
                slapi_log_access(LDAP_DEBUG_STATS,
                                 "conn=%" PRIu64 " op=%d MODRDN dn=\"%s\" newrdn=\"%s\" newsuperior=\"%s\"%s\n",
                                 pb_conn->c_connid,
                                 operation->o_opid,
                                 dn,
                                 newrdn ? newrdn : "(null)",
                                 newsuperior ? newsuperior : "(null)",
                                 proxystr ? proxystr : "");
            }

        } else {
            uint64_t connid;
            int32_t op_id;
            int32_t op_internal_id;
            int32_t op_nested_count;
            time_t start_time;

            get_internal_conn_op(&connid, &op_id, &op_internal_id, &op_nested_count, &start_time);
            if (log_format != LOG_FORMAT_DEFAULT) {
                logpb.conn_time = start_time;
                logpb.conn_id = connid;
                logpb.op_id = op_id;
                logpb.op_internal_id = op_internal_id;
                logpb.op_nested_count = op_nested_count;
                logpb.level = LDAP_DEBUG_ARGS;
                slapd_log_access_modrdn(&logpb);
            } else {
                slapi_log_access(LDAP_DEBUG_ARGS,
                                 connid==0 ? "conn=Internal(%" PRId64 ") op=%d(%d)(%d) MODRDN dn=\"%s\" newrdn=\"%s\" newsuperior=\"%s\"%s\n" :
                                             "conn=%" PRId64 " (Internal) op=%d(%d)(%d) MODRDN dn=\"%s\" newrdn=\"%s\" newsuperior=\"%s\"%s\n",
                                 connid,
                                 op_id,
                                 op_internal_id,
                                 op_nested_count,
                                 dn,
                                 newrdn ? newrdn : "(null)",
                                 newsuperior ? newsuperior : "(null)",
                                 proxystr ? proxystr : "");
            }
        }
    }

    /* If we encountered an error parsing the proxy control, return an error
     * to the client.  We do this here to ensure that we log the operation first. */
    if (proxy_err != LDAP_SUCCESS) {
        send_ldap_result(pb, proxy_err, NULL, errtext, 0, NULL);
        goto free_and_return_nolock;
    }

    /* check that the rdn is formatted correctly */
    if ((rdns = slapi_ldap_explode_rdn(newrdn, 0)) == NULL) {
        if (!internal_op) {
            slapi_log_err(SLAPI_LOG_ARGS, "op_shared_rename",
                          "conn=%" PRIu64 " op=%d MODRDN invalid new RDN (\"%s\")\n",
                          pb_conn->c_connid,
                          operation->o_opid,
                          (NULL == newrdn) ? "(null)" : newrdn);
        } else {
            slapi_log_err(SLAPI_LOG_ARGS, "op_shared_rename",
                          "conn=%s op=%d MODRDN invalid new RDN (\"%s\")\n",
                          LOG_INTERNAL_OP_CON_ID,
                          LOG_INTERNAL_OP_OP_ID,
                          (NULL == newrdn) ? "(null)" : newrdn);
        }
        send_ldap_result(pb, LDAP_INVALID_DN_SYNTAX, NULL, "invalid RDN", 0, NULL);
        goto free_and_return_nolock;
    } else {
        slapi_ldap_value_free(rdns);
    }

    /* check if created attributes are used in the new RDN */
    /* check_rdn_for_created_attrs ignores the cases */
    if (check_rdn_for_created_attrs((const char *)newrdn)) {
        send_ldap_result(pb, LDAP_INVALID_DN_SYNTAX, NULL, "invalid attribute in RDN", 0, NULL);
        goto free_and_return_nolock;
    }

    /* check that the dn is formatted correctly */
    err = slapi_dn_syntax_check(pb, newsuperior, 1);
    if (err) {
        slapi_log_err(SLAPI_LOG_ARGS, "op_shared_rename",
                      "Syntax check of newSuperior failed\n");
        if (!internal_op) {
            slapi_log_err(SLAPI_LOG_ARGS, "op_shared_rename",
                          "conn=%" PRIu64 " op=%d MODRDN invalid new superior (\"%s\")\n",
                          pb_conn->c_connid,
                          operation->o_opid,
                          newsuperior ? newsuperior : "(null)");
        } else {
            slapi_log_err(SLAPI_LOG_ARGS, "op_shared_rename",
                          "conn=%s op=%d MODRDN invalid new superior (\"%s\")\n",
                          LOG_INTERNAL_OP_CON_ID,
                          LOG_INTERNAL_OP_OP_ID,
                          newsuperior ? newsuperior : "(null)");
        }
        send_ldap_result(pb, LDAP_INVALID_DN_SYNTAX, NULL,
                         "newSuperior does not look like a DN", 0, NULL);
        goto free_and_return_nolock;
    }

    if (newsuperior != NULL) {
        slapi_log_err(SLAPI_LOG_ARGS, "op_shared_rename", "do_moddn: newsuperior (%s)\n", newsuperior);
    }

    /* target spec is used to decide which plugins are applicable for the operation */
    operation_set_target_spec(operation, sdn);

    /*
     * Construct the new DN (code sdn from backend
     * and modified to handle newsuperior)
     */
    newdn = slapi_moddn_get_newdn(sdn, newrdn, newsuperior);

    /*
     * We could be serving multiple database backends.  Select the
     * appropriate one, or send a referral to our "referral server"
     * if we don't hold it.
     */
    /* slapi_mapping_tree_select_and_check ignores the case of newdn
     * which is generated using newrdn above. */
    errorbuf[0] = '\0';
    if ((err = slapi_mapping_tree_select_and_check(pb, newdn, &be, &referral, errorbuf, sizeof(errorbuf))) != LDAP_SUCCESS) {
        send_ldap_result(pb, err, NULL, errorbuf, 0, NULL);
        goto free_and_return_nolock;
    }

    if (referral) {
        int managedsait;

        slapi_pblock_get(pb, SLAPI_MANAGEDSAIT, &managedsait);
        if (managedsait) {
            send_ldap_result(pb, LDAP_UNWILLING_TO_PERFORM, NULL,
                             "cannot update referral", 0, NULL);
            slapi_entry_free(referral);
            goto free_and_return;
        }

        send_referrals_from_entry(pb, referral);
        slapi_entry_free(referral);
        goto free_and_return;
    }

    slapi_pblock_set(pb, SLAPI_BACKEND, be);

    /* can get lastmod only after backend is selected */
    slapi_pblock_get(pb, SLAPI_BE_LASTMOD, &lastmod);

    /* if it is a replicated operation - leave lastmod attributes alone */
    slapi_mods_init(&smods, 2);
    if (!repl_op && lastmod) {
        modify_update_last_modified_attr(pb, &smods);
        slapi_pblock_set(pb, SLAPI_MODIFY_MODS, (void *)slapi_mods_get_ldapmods_passout(&smods));
    } else {
        slapi_mods_done(&smods);
    }

    /*
     * call the pre-modrdn plugins. if they succeed, call
     * the backend modrdn function. then call the
     * post-modrdn plugins.
     */
    if (plugin_call_plugins(pb, internal_op ? SLAPI_PLUGIN_INTERNAL_PRE_MODRDN_FN : SLAPI_PLUGIN_PRE_MODRDN_FN) == SLAPI_PLUGIN_SUCCESS) {
        int rc = LDAP_OPERATIONS_ERROR;
        slapi_pblock_set(pb, SLAPI_PLUGIN, be->be_database);
        set_db_default_result_handlers(pb);
        if (be->be_modrdn != NULL) {
            if ((rc = (*be->be_modrdn)(pb)) == 0) {
                Slapi_Entry *pse;
                Slapi_Entry *ecopy;
                /* we don't perform acl check for internal operations */
                /* dont update aci store for remote acis              */
                if ((!internal_op) &&
                    (!slapi_be_is_flag_set(be, SLAPI_BE_FLAG_REMOTE_DATA)))
                    plugin_call_acl_mods_update(pb, SLAPI_OPERATION_MODRDN);

                if (operation_is_flag_set(operation, OP_FLAG_ACTION_LOG_AUDIT))
                    write_audit_log_entry(pb); /* Record the operation in the audit log */

                slapi_pblock_get(pb, SLAPI_ENTRY_POST_OP, &pse);
                slapi_pblock_get(pb, SLAPI_ENTRY_PRE_OP, &ecopy);
                /* GGOODREPL persistent search system needs the changenumber, oops. */
                do_ps_service(pse, ecopy, LDAP_CHANGETYPE_MODDN, 0);
            } else {
                /* Should we also be doing a disk space check here? */
                if (operation_is_flag_set(operation, OP_FLAG_ACTION_LOG_AUDIT)) {
                    write_auditfail_log_entry(pb); /* Record the operation in the audit log */
                }
            }
        } else {
            send_ldap_result(pb, LDAP_UNWILLING_TO_PERFORM, NULL, "Function not implemented", 0, NULL);
        }

        slapi_pblock_set(pb, SLAPI_PLUGIN_OPRETURN, &rc);
        plugin_call_plugins(pb, internal_op ? SLAPI_PLUGIN_INTERNAL_POST_MODRDN_FN : SLAPI_PLUGIN_POST_MODRDN_FN);
    }

free_and_return:
    if (be)
        slapi_be_Unlock(be);
free_and_return_nolock : {
    /* Free up everything left in the PBlock */
    Slapi_Entry *pse;
    Slapi_Entry *ecopy;
    LDAPMod **mods;
    char *s;

    if (passin_args) {
        if (NULL == origsdn) {
            slapi_sdn_free(&sdn);
        }
    } else {
        slapi_pblock_get(pb, SLAPI_MODRDN_TARGET_SDN, &sdn);
        slapi_sdn_free(&sdn);
        /* get newrdn to free the string */
        slapi_pblock_get(pb, SLAPI_MODRDN_NEWRDN, &newrdn);
        slapi_ch_free_string(&newrdn);
        slapi_pblock_get(pb, SLAPI_MODRDN_NEWSUPERIOR_SDN, &newsuperiorsdn);
        slapi_sdn_free(&newsuperiorsdn);
    }
    slapi_ch_free_string(&newdn);

    slapi_pblock_get(pb, SLAPI_ENTRY_PRE_OP, &ecopy);
    slapi_entry_free(ecopy);
    slapi_pblock_get(pb, SLAPI_ENTRY_POST_OP, &pse);
    slapi_entry_free(pse);
    slapi_pblock_get(pb, SLAPI_MODIFY_MODS, &mods);
    ldap_mods_free(mods, 1);
    slapi_ch_free_string(&proxydn);
    slapi_ch_free_string(&proxystr);

    slapi_pblock_get(pb, SLAPI_URP_NAMING_COLLISION_DN, &s);
    slapi_ch_free((void **)&s);
    slapi_pblock_set(pb, SLAPI_URP_NAMING_COLLISION_DN, NULL);
}
}


/* Checks if created attributes are used in the RDN.
 * Returns 1 if created attrs are in the RDN, and
 * 0 if created attrs are not in the RDN. Returns
 * -1 if an error occurs.
 */
static int
check_rdn_for_created_attrs(const char *newrdn)
{
    int i, rc = 0;
    Slapi_RDN *rdn = NULL;
    char *value = NULL;
    char *type[] = {"modifytimestamp", "createtimestamp",
                    "creatorsname", "modifiersname", 0};

    if (newrdn && *newrdn && (rdn = slapi_rdn_new())) {
        slapi_rdn_init_dn(rdn, newrdn);
        for (i = 0; type[i] != NULL; i++) {
            if (slapi_rdn_contains_attr(rdn, type[i], &value)) {
                slapi_log_err(SLAPI_LOG_TRACE, "check_rdn_for_created_attrs",
                              "Invalid DN. RDN contains %s attribute\n", type[i]);
                rc = 1;
                break;
            }
        }
        slapi_rdn_free(&rdn);
    } else {
        slapi_log_err(SLAPI_LOG_TRACE, "check_rdn_for_created_attrs",
                      "Error allocating RDN\n");
        rc = -1;
    }

    return rc;
}
