def lookup(cur, name):
    cur.execute("SELECT * FROM users WHERE name = " + name)
