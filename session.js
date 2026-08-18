const SessionManager = {
    KEYS: {
        USER: 'sd_user_data',
        IS_LOGGED_IN: 'sd_authenticated'
    },

    saveSession: (userData) => {
        localStorage.setItem(SessionManager.KEYS.USER, JSON.stringify(userData));
        localStorage.setItem(SessionManager.KEYS.IS_LOGGED_IN, 'true');
    },

    getUser: () => {
        const data = localStorage.getItem(SessionManager.KEYS.USER);
        return data ? JSON.parse(data) : null;
    },

    /**
     * Protects a page. Accepts a single string or an array of allowed roles.
     */
    requireAuth: (requiredRole = null) => {
        const user = SessionManager.getUser();
        const isAuthenticated = localStorage.getItem(SessionManager.KEYS.IS_LOGGED_IN) === 'true';

        if (!user || !isAuthenticated) {
            window.location.replace("login.html");
            return null;
        }

        if (requiredRole) {
            const userRole = user.role.toLowerCase();
            // Handle array of roles
            if (Array.isArray(requiredRole)) {
                if (!requiredRole.map(r => r.toLowerCase()).includes(userRole)) {
                    SessionManager.logout();
                    return null;
                }
            } 
            // Handle single string role
            else if (userRole !== requiredRole.toLowerCase()) {
                SessionManager.logout();
                return null;
            }
        }
        return user;
    },

    logout: () => {
        localStorage.removeItem(SessionManager.KEYS.USER);
        localStorage.removeItem(SessionManager.KEYS.IS_LOGGED_IN);
        window.location.replace("login.html");
    }
};

window.SessionManager = SessionManager;