void id27(int y){
  int x = 42;
  while (y < 100) {
    while (x <= 100) {
      if (x == 50)
        x = 60;
      else
        x++;
    }
    y++;
  }
}