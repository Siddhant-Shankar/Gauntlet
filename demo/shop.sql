CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    signup_date TEXT NOT NULL -- YYYY-MM-DD
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price_cents INTEGER NOT NULL
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers (id),
    order_date TEXT NOT NULL, -- YYYY-MM-DD
    status TEXT NOT NULL -- 'paid', 'refunded' or 'pending'
);

CREATE TABLE order_items (
    order_id INTEGER NOT NULL REFERENCES orders (id),
    product_id INTEGER NOT NULL REFERENCES products (id),
    quantity INTEGER NOT NULL,
    PRIMARY KEY (order_id, product_id)
);

INSERT INTO customers VALUES
    (1, 'Ana Lima', 'Brazil', '2025-01-14'),
    (2, 'Ben Carter', 'USA', '2025-02-03'),
    (3, 'Chloe Martin', 'Canada', '2025-02-20'),
    (4, 'Dev Patel', 'India', '2025-03-08'),
    (5, 'Emma Davis', 'USA', '2025-03-22'),
    (6, 'Femi Okafor', 'Nigeria', '2025-04-10'),
    (7, 'Grace Kim', 'USA', '2025-05-05'),
    (8, 'Hugo Silva', 'Brazil', '2025-06-18');

INSERT INTO products VALUES
    (1, 'Notebook', 'stationery', 450),
    (2, 'Pen set', 'stationery', 1200),
    (3, 'Desk lamp', 'home', 3500),
    (4, 'Coffee mug', 'home', 900),
    (5, 'Backpack', 'bags', 5400),
    (6, 'Water bottle', 'bags', 1700);

INSERT INTO orders VALUES
    (1, 1, '2025-02-01', 'paid'),
    (2, 2, '2025-02-15', 'paid'),
    (3, 2, '2025-03-02', 'refunded'),
    (4, 3, '2025-03-10', 'paid'),
    (5, 4, '2025-03-28', 'paid'),
    (6, 5, '2025-04-02', 'paid'),
    (7, 1, '2025-04-15', 'pending'),
    (8, 6, '2025-04-30', 'paid'),
    (9, 7, '2025-05-12', 'paid'),
    (10, 2, '2025-05-20', 'paid'),
    (11, 5, '2025-06-01', 'refunded'),
    (12, 3, '2025-06-09', 'paid'),
    (13, 7, '2025-06-21', 'paid'),
    (14, 4, '2025-06-30', 'pending');

INSERT INTO order_items VALUES
    (1, 1, 3), (1, 4, 1),
    (2, 5, 1),
    (3, 3, 1),
    (4, 2, 2), (4, 1, 5),
    (5, 6, 2),
    (6, 5, 1), (6, 4, 2),
    (7, 3, 1),
    (8, 1, 10),
    (9, 3, 1), (9, 6, 1),
    (10, 2, 1), (10, 4, 4),
    (11, 5, 1),
    (12, 6, 3),
    (13, 1, 2), (13, 2, 1),
    (14, 4, 1);
