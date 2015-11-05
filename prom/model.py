# stdlib
import inspect
import sys
import datetime

# first party
from .query import Query
from . import decorators, utils
from .interface import get_interface
from .config import Schema


class Orm(object):
    """
    this is the parent class of any model Orm class you want to create that can access the db

    NOTE -- you must set the schema class as a class property (not an instance property)

    example -- create a user class

        import prom

        class User(prom.Orm):

            schema = prom.Schema(
                "user_table_name",
                username=(str, True),
                password=(str, True)
                email=(str, True)
                unique_user=('username') # set a unique index on user
                index_email=('email') # set a normal index on email
            )

        # create a user
        u = User(username='foo', password='awesome_and_secure_pw_hash', email='foo@bar.com')
        u.set()

        # query for our new user
        u = User.query.is_username('foo').get_one()
        print u.username # foo
    """

    connection_name = ""
    """the name of the connection to use to retrieve the interface"""

    query_class = Query
    """the class this Orm will use to create Query instances to query the db"""

    iterator_class = None
    """the class this Orm will use for iterating through results returned from db"""

    @decorators.classproperty
    def table_name(cls):
        return cls.__name__.lower()

    @decorators.classproperty
    def schema(cls):
        """the Schema() instance that this class will derive all its db info from"""
        return Schema.get_instance(cls)

    @decorators.classproperty
    def interface(cls):
        """
        return an Interface instance that can be used to access the db

        return -- Interface() -- the interface instance this Orm will use
        """
        return get_interface(cls.connection_name)

    @decorators.classproperty
    def query(cls):
        """
        return a new Query instance ready to make a db call using the child class

        example -- fluid interface
            results = Orm.query.is_foo('value').desc_bar().get()

        return -- Query() -- every time this is called a new query instance is created using cls.query_class
        """
        query_class = cls.query_class
        return query_class(orm=cls)

    @property
    def pk(self):
        """wrapper method to return the primary key, None if the primary key is not set"""
        return getattr(self, self.schema.pk, None)

    @property
    def created(self):
        """wrapper property method to return the created timestamp"""
        return getattr(self, self.schema._created, None)

    @property
    def updated(self):
        """wrapper property method to return the updated timestamp"""
        return getattr(self, self.schema._updated, None)

    @property
    def fields(self):
        """
        return all the fields and their raw values for this Orm instance. This
        property returns a dict with the field names and their current values

        if you want to control the values for outputting to an api, use .jsonable()
        """
        return {k:getattr(self, k, None) for k in self.schema.fields}

    def __init__(self, fields=None, **fields_kwargs):
        self.reset_modified()
        self.hydrate(fields, fields_kwargs)

    @classmethod
    def create(cls, fields=None, **fields_kwargs):
        """
        create an instance of cls with the passed in fields and set it into the db

        fields -- dict -- field_name keys, with their respective values
        **fields_kwargs -- dict -- if you would rather pass in fields as name=val, that works also
        """
        # NOTE -- you cannot use populate here because populate alters modified fields
        instance = cls(fields, **fields_kwargs)
        instance.save()
        return instance

    @classmethod
    def populate(cls, fields=None, **fields_kwargs):
        """
        create an instance of cls with the passed in fields but don't set it into the db or mark the passed
        in fields as modified, this is used by the Query class to hydrate objects

        fields -- dict -- field_name keys, with their respective values
        **fields_kwargs -- dict -- if you would rather pass in fields as name=val, that works also
        """
        instance = cls(fields, **fields_kwargs)
        instance.reset_modified()
        return instance

    def __setattr__(self, field_name, field_val):
        if field_name in self.schema.fields:
            if field_val is not None:
                field_val = self._normalize_field(field_name, field_val)

            self.modified_fields.add(field_name)

        super(Orm, self).__setattr__(field_name, field_val)

    def __int__(self):
        return int(self.pk)

    def insert(self):
        """persist the field values of this orm"""
        fields = self.get_modified()

        for field_name in self.schema.required_fields.keys():
            if field_name not in fields:
                raise KeyError("Missing required field {}".format(field_name))

        q = self.query
        q.set_fields(fields)
        fields = q.set()
        self.modify(fields)
        self.reset_modified()
        return True

    def update(self):
        """re-persist the updated field values of this orm that has a primary key"""
        fields = self.get_modified()

        q = self.query
        _id_name = self.schema.pk
        _id = self.pk
        if _id:
            q.is_field(_id_name, _id)

        q.set_fields(fields)
        fields = q.set()
        self.modify(fields)
        self.reset_modified()
        return True

    def set(self): return self.save()
    def save(self):
        """
        persist the fields in this object into the db, this will update if _id is set, otherwise
        it will insert

        see also -- .insert(), .update()
        """
        ret = False

        _id_name = self.schema.pk
        # we will only use the primary key if it hasn't been modified
        _id = None
        if _id_name not in self.modified_fields:
            _id = self.pk

        if _id:
            ret = self.update()
        else:
            ret = self.insert()

        return ret

    def delete(self):
        """delete the object from the db if _id is set"""
        ret = False
        q = self.query
        _id = self.pk
        _id_name = self.schema.pk
        if _id:
            self.query.is_field(_id_name, _id).delete()
            # get rid of _id
            delattr(self, _id_name)

            # mark all the fields that still exist as modified
            self.reset_modified()
            for field_name in self.schema.fields:
                if hasattr(self, field_name):
                    self.modified_fields.add(field_name)

            ret = True

        return ret

    def get_modified(self):
        """return the modified fields and their new values"""
        fields = {}
        for field_name in self.modified_fields:
            fields[field_name] = getattr(self, field_name)

        return fields

    def modify(self, fields=None, **fields_kwargs):
        """update the fields of this instance with the values in dict fields"""
        fields = utils.make_dict(fields, fields_kwargs)
        for field_name, field_val in fields.items():
            in_schema = field_name in self.schema.fields
            if in_schema:
                setattr(self, field_name, field_val)

    def is_modified(self):
        """true if a field has been changed from its original value, false otherwise"""
        return len(self.modified_fields) > 0

    def reset_modified(self):
        """
        reset field modification tracking

        this is handy for when you are loading a new Orm with the results from a query and
        you don't want set() to do anything, you can Orm(**fields) and then orm.reset_modified() to
        clear all the passed in fields from the modified list
        """
        self.modified_fields = set()

    def hydrate(self, fields=None, **fields_kwargs):
        """figure out what value to give every field in the Orm's schema, this means
        that if a field is missing from the passed in fields dict, it will be set
        to None for this instance, if you just want to deal with fields that you
        passed in manipulating this instance, use .modify()"""
        fields = utils.make_dict(fields, fields_kwargs)
        schema_fields = set(self.schema.fields.keys())
        for field_name, field_val in fields.items():
            in_schema = field_name in self.schema.fields
            if in_schema:
                setattr(self, field_name, field_val)
                schema_fields.discard(field_name)

        # pick up any stragglers and set them to None:
        for field_name in schema_fields:
            if not field_name.startswith('_'):
                setattr(self, field_name, None)
                self.modified_fields.discard(field_name)

    def jsonable(self, *args, **options):
        """
        return a public version of this instance that can be jsonified

        Note that this does not return _id, _created, _updated, the reason why is
        because lots of times you have a different name for _id (like if it is a 
        user object, then you might want to call it user_id instead of _id) and I
        didn't want to make assumptions

        note 2, I'm not crazy about the name, but I didn't like to_dict() and pretty
        much any time I need to convert the object to a dict is for json, I kind of
        like dictify() though, but I've already used this method in so many places
        """
        d = {}
        def default_field_type(field_type):
            r = ''
            if issubclass(field_type, int):
                r = 0
            elif issubclass(field_type, bool):
                r = False
            elif issubclass(field_type, float):
                r = 0.0

            return r

        for field_name, field_info in self.schema.normal_fields.iteritems():
            try:
                d[field_name] = getattr(self, field_name, None)
                if d[field_name]:
                    if isinstance(d[field_name], datetime.date):
                        d[field_name] = str(d[field_name])
                    elif isinstance(d[field_name], datetime.datetime):
                        d[field_name] = str(d[field_name])

                else:
                    d[field_name] = default_field_type(field_info['type'])

            except AttributeError:
                d[field_name] = default_field_type(field_info['type'])

        return d

    def _normalize_field(self, field_name, field_val):
        """
        you can override this to modify/check certain values as they are modified

        NOTE -- this will not be called with a None value, a None value is assumed
        to be NULL and that you don't have to do any normalizing, so it gets set
        directly

        field_name -- string -- the field's name
        field_val -- mixed -- the field's value
        return -- mixed -- the field_val, with any changes
        """
        return field_val

    @classmethod
    def install(cls):
        """install the Orm's table using the Orm's schema"""
        return cls.interface.set_table(cls.schema)

