from fabric.api import task, local
from fabfile.utilities import notify, warn, alert
from fabfile.config import CONFIG


@task
def create(user=CONFIG['db_user'], name=CONFIG['db_name']):
    notify(u'Creating a new database.')
    local('createdb --template template0 --encoding UTF-8 --owner {user} {name}'.format(user=user, name=name))


@task
def drop(name=CONFIG['db_name']):
    alert(u'Dropping the database.')
    local('dropdb {name}'.format(name=name))


@task
def rebuild(user=CONFIG['db_user'], name=CONFIG['db_name']):
    warn(u'Rebuilding the database.')
    drop(name)
    create(user, name)


@task
def createuser(name=CONFIG['db_user']):
    notify(u'Creating a new database user.')
    local('createuser --createdb {name}'.format(name=name))
