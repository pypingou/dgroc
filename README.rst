dgroc
=====

:Author: Pierre-Yves Chibon <pingou@pingoured.fr>


dgroc: Daily Git Rebuild On Copr

This project aims at easily provide daily build of a project tracked via git and
made available via `copr <http://copr.fedoraproject.org>`_.

Get it running
==============

* Retrieve the sources::

    git clone git//github.com:pypingou/dgroc.git


* Create the configuration file ``~/.config/dgroc``

* Fill the configuration file, for example::

    [main]
    fas_user = pingou
    copr_url = https://copr.fedoraproject.org/
    upload_command = cp %s /var/www/html/subsurface/
    upload_url = http://my_server/subsurface/%s
    no_ssl_check = True
    
    [subsurface]
    git_url = git://subsurface.hohndel.org/subsurface.git
    git_folder = /tmp/subsurface/
    spec_file = ~/GIT/subsurface/subsurface.spec

* Run it::

  ./dgroc.py

For more information/output run ``./dgroc.py --debug``
