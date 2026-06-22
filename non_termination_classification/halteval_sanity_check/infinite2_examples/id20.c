typedef struct s_ent {
    unsigned int val;
    struct s_ent *next;
} ent_t;

void id19(ent_t *param);

void id20() {
    ent_t one;
    ent_t two;
    one.val = 1;
    one.next = &two;
    two.val = 2;
    two.next = 0; // or NULL
    id19(&one);
}
