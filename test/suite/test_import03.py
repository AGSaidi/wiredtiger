#!/usr/bin/env python
#
# Public Domain 2014-2020 MongoDB, Inc.
# Public Domain 2008-2014 WiredTiger, Inc.
#
# This is free and unencumbered software released into the public domain.
#
# Anyone is free to copy, modify, publish, use, compile, sell, or
# distribute this software, either in source code form or as a compiled
# binary, for any purpose, commercial or non-commercial, and by any
# means.
#
# In jurisdictions that recognize copyright laws, the author or authors
# of this software dedicate any and all copyright interest in the
# software to the public domain. We make this dedication for the benefit
# of the public at large and to the detriment of our heirs and
# successors. We intend this dedication to be an overt act of
# relinquishment in perpetuity of all present and future rights to this
# software under copyright law.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.
#
# test_import03.py
# Import a table into a running database.

import os, re, shutil
import wiredtiger, wttest

def timestamp_str(t):
    return '%x' % t

class test_import03(wttest.WiredTigerTestCase):
    conn_config = 'cache_size=50MB,log=(enabled),statistics=(all)'
    session_config = 'isolation=snapshot'

    def update(self, uri, key, value_tuple, commit_ts):
        cursor = self.session.open_cursor(uri)
        self.session.begin_transaction()
        cursor.set_key(key)
        cursor.set_value(*value_tuple)
        cursor.insert()
        self.session.commit_transaction('commit_timestamp=' + timestamp_str(commit_ts))
        cursor.close()

    def check(self, uri, key, value_tuple, read_ts):
        # Read the entire record.
        cursor = self.session.open_cursor(uri)
        self.session.begin_transaction('read_timestamp=' + timestamp_str(read_ts))
        cursor.set_key(key)
        self.assertEqual(0, cursor.search())
        self.assertEqual(list(value_tuple), cursor.get_value())
        self.session.rollback_transaction()
        cursor.close()

        # Now let's test something table specific like a projection.
        # We're unpacking only the country and population columns here.
        projection_uri = uri + '(country,population)'
        cursor = self.session.open_cursor(projection_uri)
        self.session.begin_transaction('read_timestamp=' + timestamp_str(read_ts))
        cursor.set_key(key)
        self.assertEqual(0, cursor.search())
        self.assertEqual([value_tuple[0], value_tuple[2]], cursor.get_value())
        self.session.rollback_transaction()
        cursor.close()

    # We know the ID can be different between configs, so just remove it from comparison.
    # Everything else should be the same.
    def config_compare(self, aconf, bconf):
        a = re.sub('id=\d+,?', '', aconf)
        a = (re.sub('\w+=\(.*?\)+,?', '', a).strip(',').split(',') +
             re.findall('\w+=\(.*?\)+', a))
        b = re.sub('id=\d+,?', '', bconf)
        b = (re.sub('\w+=\(.*?\)+,?', '', b).strip(',').split(',') +
             re.findall('\w+=\(.*?\)+', b))
        self.assertTrue(a.sort() == b.sort())

    # Helper for populating a database to simulate importing files into an existing database.
    def populate(self):
        # Create file:test_import03_[1-100].
        for fileno in range(1, 100):
            uri = 'file:test_import03_{}'.format(fileno)
            self.session.create(uri, 'key_format=i,value_format=S')
            cursor = self.session.open_cursor(uri)
            # Insert keys [1-100] with value 'foo'.
            for key in range(1, 100):
                cursor[key] = 'foo'
            cursor.close()

    def copy_file(self, file_name, old_dir, new_dir):
        old_path = os.path.join(old_dir, file_name)
        if os.path.isfile(old_path) and "WiredTiger.lock" not in file_name and \
            "Tmplog" not in file_name and "Preplog" not in file_name:
            shutil.copy(old_path, new_dir)

    def test_table_import(self):
        original_db_table = 'original_db_table'
        uri = 'table:' + original_db_table

        create_config = 'allocation_size=512,key_format=r,log=(enabled=true),value_format=SSi,' \
            'columns=(id,country,capital,population)'
        self.session.create(uri, create_config)

        key1 = b'1'
        key2 = b'2'
        key3 = b'3'
        key4 = b'4'
        value1 = ('Australia', 'Canberra', 1)
        value2 = ('Japan', 'Tokyo', 2)
        value3 = ('Italy', 'Rome', 3)
        value4 = ('China', 'Beijing', 4)

        # Add some data.
        self.update(uri, key1, value1, 10)
        self.update(uri, key2, value2, 20)

        # Perform a checkpoint.
        self.session.checkpoint()

        # Add more data.
        self.update(uri, key3, value3, 30)
        self.update(uri, key4, value4, 40)

        # Perform a checkpoint.
        self.session.checkpoint()

        # Export the metadata for the table.
        original_db_file_uri = 'file:' + original_db_table + '.wt'
        c = self.session.open_cursor('metadata:', None, None)
        original_db_table_config = c[uri]
        original_db_file_config = c[original_db_file_uri]
        c.close()

        self.printVerbose(3, '\nFILE CONFIG\n' + original_db_file_config)

        # Contruct the config string.
        import_config = '{},import=(enabled,repair=false,file_metadata=({}))'.format(
            original_db_table_config, original_db_file_config)

        # Close the connection.
        self.close_conn()

        # Create a new database and connect to it.
        newdir = 'IMPORT_DB'
        shutil.rmtree(newdir, ignore_errors=True)
        os.mkdir(newdir)
        self.conn = self.setUpConnectionOpen(newdir)
        self.session = self.setUpSessionOpen(self.conn)

        # Make a bunch of files and fill them with data.
        self.populate()

        # Copy over the datafiles for the object we want to import.
        self.copy_file(original_db_table + '.wt', '.', newdir)

        # Import the table.
        self.session.create(uri, import_config)

        # Verify object.
        self.session.verify(uri)

        # Check that the previously inserted values survived the import.
        self.check(uri, key1, value1, 10)
        self.check(uri, key2, value2, 20)
        self.check(uri, key3, value3, 30)
        self.check(uri, key4, value4, 40)

        # Compare checkpoint information.
        c = self.session.open_cursor('metadata:', None, None)
        current_db_table_config = c[uri]
        c.close()
        self.config_compare(original_db_table_config, current_db_table_config)

        key5 = b'5'
        key6 = b'6'
        value5 = ('Germany', 'Berlin', 5)
        value6 = ('South Korea', 'Seoul', 6)

        # Add some data and check that the table operates as usual after importing.
        self.update(uri, key5, value5, 50)
        self.update(uri, key6, value6, 60)

        self.check(uri, key5, value5, 50)
        self.check(uri, key6, value6, 60)

        # Perform a checkpoint.
        self.session.checkpoint()

if __name__ == '__main__':
    wttest.run()
