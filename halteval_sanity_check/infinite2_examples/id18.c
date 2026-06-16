void id18(int state) {
  while (state != 5) {
    switch (state) {
    case 1:
      state = 2;
      break;
    case 2:
      state = 1;
      break;
    case 3:
      state = 4;
      break;
    case 4:
      state = 5;
    default:
      return;
    }
  }
}