from koschei.frontend import app as application
import koschei.views
import koschei.auth

if __name__ == '__main__':
    application.run(debug=True)
