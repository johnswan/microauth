"""
This module determines the behavior of API Keys within the system.
You may also want to check the definition of API keys in models.py.
"""
import re
from flask import request
from sqlalchemy import and_
from flask.ext import restful
from microauth import app, db
from microauth.models import *
from microauth.utils import get, gzipped
from flask.ext.restful import reqparse, abort

def auth():
    if 'Authorization' in request.headers:
        key_str = request.headers['Authorization'].replace('Basic ', '')
        key = APIKey.query.filter(APIKey.key == key_str).first()
        if key and key.active: return key
    abort(401, message="Invalid API Key.")

class KeyCollection(restful.Resource):

    @gzipped
    def get(self):
        key = auth()
        response = key.jsonify(with_objs=True)

        if key.name == app.config['MASTER_KEY_NAME'] or key.systemwide:
            response['system'] = {}

        if key.name == app.config['MASTER_KEY_NAME']:
            keys = []
            for i in APIKey.query.all(): keys.append(i.name)
            response['system']['keys'] = keys
            response['system']['permit_new'] = app.config['PERMIT_NEW']

        if key.systemwide:
            global_users  = [_.username for _ in User.query.filter(User.key == None).all()]
            global_groups = [_.name     for _ in Group.query.filter(Group.key == None).all()]
            global_privs  = [_.name     for _ in Priv.query.filter(Priv.key == None).all()]
            response['system']['users']      = global_users
            response['system']['groups']     = global_groups
            response['system']['privileges'] = global_privs

        return [response]

    @gzipped
    def put(self):
        """This method creates keys under the provided name,
            presuming config['PERMIT_NEW'] is enabled or the master key is in use."""
        key = None
        parser = reqparse.RequestParser()
        parser.add_argument("name",type=str, help="Name associated with the key", required=True)
        args = parser.parse_args()

        if 'Authorization' in request.headers:
            key_str = request.headers['Authorization'].replace('Basic ', '')
            key = APIKey.query.filter(APIKey.key == key_str).first()

        if (key and key.name == app.config['MASTER_KEY_NAME']) or app.config['PERMIT_NEW']:
            # Permit only simple names (character limit, alphanumeric)
            if re.match("^$|\s+[a-zA-Z0-9_]+$",args.name) or len(args.name) > 60:
                abort(422, message="Invalid key name. Must contain alphanumeric characters.")
            # Determine if already exists
            key = APIKey.query.filter(APIKey.name == args.name).first()

            if key: abort(403, message="A key already exists with this name.")

            key = APIKey(name=args.name)
            db.session.add(key)
            db.session.commit()

            return key.jsonify(with_key_str=True), 201

        abort(403, message="This server isn't currently generating new keys.")

    @gzipped
    def post(self):
        "This method is for updating existing API keys via the master key."

        key = auth()

        parser = reqparse.RequestParser()
        parser.add_argument("key",        type=str, help="API Key")
        parser.add_argument("name",       type=str, help="Name associated with the key")
        parser.add_argument("permit_new", type=bool, help="Determines whether new API keys can be created.")
        parser.add_argument("systemwide", type=bool, help="Set the systemwide flag on a key.", default=None)
        parser.add_argument("global_delete", type=bool, help="Determines whether systemwide keys can delete systemwide objects.", default=None)
        parser.add_argument("active",        type=bool, help="Determines whether a key is active or not.", default=None)
        args = parser.parse_args()

        if key.name != app.config['MASTER_KEY_NAME']: abort(403)

        response={}
        subject = None

        if args.key and args.name:
            subject = APIKey.query.filter(APIKey.key == args.key).first()
            if APIKey.query.filter(APIKey.name == args.name).first():
                return {'message':"A key already exists with this name."}, 304
            subject.name = args.name
        elif args.name and not args.key:
            subject = APIKey.query.filter(APIKey.name == args.name).first()
        elif args.key and not args.name:
            subject = APIKey.query.filter(APIKey.key == args.key).first()

        if not subject: abort(404)

        if subject.name == app.config['MASTER_KEY_NAME']: abort(403)

        if subject:
            if args.active        or args.active        == False: subject.active     = args.active
            if args.systemwide    or args.systemwide    == False: subject.systemwide = args.systemwide
            if args.global_delete or args.global_delete == False: subject.global_del = args.global_delete

            response['key'] = subject.jsonify(with_key_str=True)
            db.session.add(subject)

        if (args.permit_new or args.permit_new == False) and key.name == app.config['MASTER_KEY_NAME']:
            app.config['PERMIT_NEW'] = args.permit_new
            response['system'] = {}
            response['system']['permit_new'] = app.config['PERMIT_NEW']

        db.session.commit()
        return response

    @gzipped
    def delete(self):
        # http://docs.sqlalchemy.org/en/rel_0_9/orm/tutorial.html#configuring-delete-delete-orphan-cascade
        key = auth()

        parser = reqparse.RequestParser()
        parser.add_argument("key",      type=str,  help="API Key")
        parser.add_argument("reparent", type=bool, help="Reparent local objects to the system.", default=False)
        args = parser.parse_args()

        target = APIKey.query.filter(APIKey.key == args.key).first()
        if not target: abort(404, message="Unrecognized key.")

        if args.key != key.key and key.name != app.config['MASTER_KEY_NAME']:
            abort(403, message="You do not have permission to remove this key.")
        if key.name == app.config['MASTER_KEY_NAME'] and args.key == key.key:
            abort(403, message="You are attempting to delete the master key.")

        if args.reparent == True and target.systemwide:
            for u in target.users:
                if not User.query.filter(and_(User.username == u.username, User.key == None)).first():
                    del u.key
            for r in target.groups:
                if not Group.query.filter(and_(Group.name == r.name, Group.key == None)).first():
                    del r.key
            for p in target.users:
                if not Priv.query.filter(and_(Priv.name == p.name, Priv.key == None)).first():
                    del p.key
        else:
            for u in target.users: db.session.delete(u)
            for r in target.groups: db.session.delete(r)
            for p in target.users: db.session.delete(p)

        db.session.delete(target)
        db.session.commit()
        return {}, 204

class KeyResource(restful.Resource):
    def get(self, name):
        key = auth()
        if key.name != app.config['MASTER_KEY_NAME'] and name != key.name:
            abort(403)

        target =  APIKey.query.filter_by(name=name).first()
        if target: return target.jsonify(with_objs=True, with_key_str=True)
        abort(404, message="Unrecognised key.")

    @gzipped
    def delete(self, name):
        # http://docs.sqlalchemy.org/en/rel_0_9/orm/tutorial.html#configuring-delete-delete-orphan-cascade
        key = auth()

        parser = reqparse.RequestParser()
        parser.add_argument("key",      type=str,  help="API Key")
        parser.add_argument("reparent", type=bool, help="Reparent local objects to the system.", default=False)
        args = parser.parse_args()

        target = APIKey.query.filter(APIKey.name == name).first()
        if not target: abort(404, message="Unrecognized key.")

        if args.key != key.key and key.name != app.config['MASTER_KEY_NAME']:
            abort(403, message="You do not have permission to remove this key.")
        if key.name == app.config['MASTER_KEY_NAME'] and args.key == key.key:
            abort(403, message="You are attempting to delete the master key.")

        if args.reparent == True and target.systemwide:
            for u in target.users:
                if not User.query.filter(and_(User.username == u.username, User.key == None)).first():
                    del u.key
            for r in target.groups:
                if not Group.query.filter(and_(Group.name == r.name, Group.key == None)).first():
                    del r.key
            for p in target.users:
                if not Priv.query.filter(and_(Priv.name == p.name, Priv.key == None)).first():
                    del p.key
        else:
            for u in target.users:  db.session.delete(u)
            for r in target.groups: db.session.delete(r)
            for p in target.users:  db.session.delete(p)

        db.session.delete(target)
        db.session.commit()
        return {}, 204

