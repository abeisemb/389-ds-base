/** BEGIN COPYRIGHT BLOCK
 * Copyright (C) 2001 Sun Microsystems, Inc. Used by permission.
 * Copyright (C) 2005 Red Hat, Inc.
 * All rights reserved.
 *
 * License: GPL (version 3 or any later version).
 * See LICENSE for details. 
 * END COPYRIGHT BLOCK **/

#ifdef HAVE_CONFIG_H
#  include <config.h>
#endif

/***********************************************************************
** NAME
**  getfilelist.c
**
** DESCRIPTION
**
**
** AUTHOR
**  Rich Megginson <richm@netscape.com>
**
***********************************************************************/

/***********************************************************************
** Includes
***********************************************************************/

#include "prio.h"
#include "slap.h"
#include "avl.h"

struct data_wrapper {
	char **list;
	int n;
	int max;
	const char *dirname;
};

static int
add_file_to_list(caddr_t data, caddr_t arg)
{
	struct data_wrapper *dw = (struct data_wrapper *)arg;
	if (dw) {
		/* max is number of entries; the range of n is 0 - max-1 */
		PR_ASSERT(dw->n <= dw->max);
		PR_ASSERT(dw->list);
		PR_ASSERT(data);
		/* this strdup is free'd by free_filelist */
		dw->list[dw->n++] = slapi_ch_smprintf("%s/%s", dw->dirname, data);
		return 0;
	}

	return -1;
}

static int
free_string(caddr_t data)
{
	slapi_ch_free((void **)&data);
       return 0;
}

static int
file_is_type_x(const char *dirname, const char *filename, PRFileType x)
{
	struct PRFileInfo64 inf;
	int status = 0;
	char *fullpath = slapi_ch_smprintf("%s/%s", dirname, filename);
	if (PR_SUCCESS == PR_GetFileInfo64(fullpath, &inf) &&
		inf.type == x)
		status = 1;

	slapi_ch_free((void **)&fullpath);

	return status;
}

/* return true if the given path and file corresponds to a directory */
static int
is_a_dir(const char *dirname, const char *filename)
{
	return file_is_type_x(dirname, filename, PR_FILE_DIRECTORY);
}

/* return true if the given path and file corresponds to a regular file */
static int
is_a_file(const char *dirname, const char *filename)
{
	return file_is_type_x(dirname, filename, PR_FILE_FILE);
}

static int
matches(const char *filename, const char *pattern)
{
	Slapi_Regex *re = NULL;
	int match = 0;
	const char *error = NULL;

	if (!pattern)
		return 1; /* null pattern matches everything */

	/* Compile the pattern */
	re = slapi_re_comp( pattern, &error );
	if (re) {
		/* Matches the compiled pattern against the filename */
		match = slapi_re_exec( re, filename, -1 /* no time limit */ );
		slapi_re_free( re );
	}

	return match;
}

/**
 * getfilelist will return a list of all files and directories in the
 * given directory matching the given pattern.  If dirname is NULL, the
 * current directory "." will be used.  If the pattern is NULL, all files
 * and directories will be returned.  The additional integer arguments
 * control which files and directories are selected.  The default value
 * for all of them is 0, which will not return hidden files (e.g. files
 * beginning with . on unix), but will return both files and directories
 * If nofiles is non-zero, only directory names will be returned.  If
 * nodirs is non-zero, only filenames will be returned.
 * The pattern is a grep style regular expression, not a shell or command
 * interpreter style regular expression.  For example, to get all files ending
 * in .ldif, use ".*\\.ldif" instead of "*.ldif"
 * The return value is a NULL terminated array of names.
 */
char **
get_filelist(
	const char *dirname, /* directory path; if NULL, uses "." */
	const char *pattern, /* grep (not shell!) file pattern regex */
	int hiddenfiles, /* if true, return hidden files and directories too */
	int nofiles, /* if true, do not return files */
	int nodirs /* if true, do not return directories */
)
{
	Avlnode *filetree = 0;
	PRDir *dirptr = 0;
	PRDirEntry *dirent = 0;
	PRDirFlags dirflags = PR_SKIP_BOTH & PR_SKIP_HIDDEN;
	char **retval = 0;
	int num = 0;
	struct data_wrapper dw;

	if (!dirname)
		dirname = ".";

	if (hiddenfiles)
		dirflags = PR_SKIP_BOTH;

	if (!(dirptr = PR_OpenDir(dirname))) {
		return NULL;
	}

	/* read the directory entries into an ascii sorted avl tree */
	for (dirent = PR_ReadDir(dirptr, dirflags); dirent ;
		 dirent = PR_ReadDir(dirptr, dirflags)) {

		if (nofiles && is_a_file(dirname, dirent->name))
			continue;

		if (nodirs && is_a_dir(dirname, dirent->name))
			continue;

		if (1 == matches(dirent->name, pattern)) {
			/* this strdup is free'd by free_string */
			char *newone = slapi_ch_strdup(dirent->name);
			avl_insert(&filetree, newone, strcmp, 0);
			num++;
		}
	}
	PR_CloseDir(dirptr);

	/* allocate space for the list */
	retval = (char **)slapi_ch_calloc(num+1, sizeof(char *));

	/* traverse the avl tree and copy the filenames into the list */
	dw.list = retval;
	dw.n = 0;
	dw.max = num;
	dw.dirname = dirname;
	(void)avl_apply(filetree, add_file_to_list, &dw, -1, AVL_INORDER);
	retval[num] = 0; /* set last entry to null */

	/* delete the avl tree and all its data */
	avl_free(filetree, free_string);

	return retval;
}


void
free_filelist(char **filelist)
{
	int ii;
	for (ii = 0; filelist && filelist[ii]; ++ii)
		slapi_ch_free((void **)&filelist[ii]);

	slapi_ch_free((void **)&filelist);
}

/**
 * Returns a list of files in order of "priority" where priority is defined
 * as:
 * The first two characters in the filename are digits representing a
 * number from 00 to 99.  The lower the number the higher the
 * priority.  For example, 00 is in the list before 01, and 99 is the
 * last item in the list.  The ordering of files with the same
 * priority cannot be guaranteed.  The pattern is the grep style
 * regular expression of filenames to match which is applied to the
 * end of the string.  If you are a Solaris person, you may recognize
 * this as the rules for init level initialization using shell scripts
 * under /etc/rcX.d/
 */
char **
get_priority_filelist(const char *directory, const char *pattern)
{
	char *basepattern = "^[0-9][0-9]";
	char *genericpattern = ".*"; /* used if pattern is null */
	char *bigpattern = 0;
	char **retval = 0;

	if (!pattern)
		pattern = genericpattern;

	bigpattern = slapi_ch_smprintf("%s%s", basepattern, pattern);

	retval = get_filelist(directory, bigpattern, 0, 0, 1);

	slapi_ch_free((void **)&bigpattern);

	return retval;
}
